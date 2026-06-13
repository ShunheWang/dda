#!/usr/bin/env python3
"""
DDA (DB Deadlock Agent) — 死锁检测与 Victim Selection
========================================================

在 rookieDB 外部监控锁状态，检测死锁，选定 victim，解除死锁。

阶段一：两种固定规则（Min Locks / Youngest First）
阶段二：LLM Victim Selection（后续实现）

运行方式:
  1. 先启动 rookieDB Server: java -cp target/classes edu.berkeley.cs186.database.cli.Server &
  2. python dda_basic.py
"""

import asyncio
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class HeldLock:
    """事务持有的一个锁。"""
    trans_num: int
    lock_type: str    # S | X | IS | IX | SIX
    resource: str     # 资源名，如 "db://tableA"


@dataclass
class WaitingRequest:
    """等待队列中的一个锁请求。"""
    trans_num: int
    lock_type: str
    resource: str


@dataclass
class LockSnapshot:
    """\alllocks 输出解析后的锁状态快照。"""
    held_locks: dict[int, list[HeldLock]]      # transNum → 持有的锁
    waiting: dict[str, list[WaitingRequest]]    # resource → 等待队列
    trans_times: dict[int, int]                 # transNum → startTime (epoch ms)
    raw_text: str                               # 原始输出文本


@dataclass
class WaitForGraph:
    """等待图。边 (u, v) 表示 u 在等待 v 释放锁。"""
    nodes: set[int] = field(default_factory=set)
    edges: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Cycle:
    """WFG 中的一个有向环。"""
    transactions: list[int]  # 环上事务序列，首尾相同


# =============================================================================
# LockParser — 解析 \alllocks 输出
# =============================================================================


class LockParser:
    """将 \alllocks 原始文本解析为 LockSnapshot。"""

    # 匹配 Lock: T1: X(db://tableA)
    LOCK_RE = re.compile(r'T(\d+):\s*(\w+)\((.+?)\)')

    # 匹配 LockRequest: Request for T1: X(db://tableA) (releasing [...])
    WAIT_RE = re.compile(r'Request for T(\d+):\s*(\w+)\((.+?)\)')

    # 匹配 transactionTimes: {1=123, 2=456}
    TIME_RE = re.compile(r'(\d+)=(\d+)')

    def parse(self, raw_text: str) -> Optional[LockSnapshot]:
        """解析 \alllocks 输出。失败返回 None。"""
        try:
            held_locks: dict[int, list[HeldLock]] = {}
            waiting: dict[str, list[WaitingRequest]] = {}
            trans_times: dict[int, int] = {}

            in_resources = False

            for line in raw_text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue

                # 进入 resourceEntries 段
                if line.startswith('resourceEntries:'):
                    in_resources = True
                    continue

                # 解析资源行: resource => Active Locks: [...], Queue: [...]
                if in_resources and '=>' in line:
                    self._parse_resource_line(line, held_locks, waiting)
                    continue

                # resourceEntries 段结束（遇到 transactionTimes 或空行后非资源行）
                if in_resources and ':' in line and '=>' not in line:
                    in_resources = False

                # transactionTimes 行
                if 'transactionTimes:' in line:
                    for m in self.TIME_RE.finditer(line):
                        trans_times[int(m.group(1))] = int(m.group(2))

            return LockSnapshot(
                held_locks=held_locks,
                waiting=waiting,
                trans_times=trans_times,
                raw_text=raw_text,
            )
        except Exception as e:
            print(f"  [Parser] 解析失败: {e}")
            return None

    def _parse_resource_line(
        self,
        line: str,
        held_locks: dict[int, list[HeldLock]],
        waiting: dict[str, list[WaitingRequest]],
    ) -> None:
        """解析 resourceEntries 中的一行。"""
        # 拆分: "resource => Active Locks: [...], Queue: [...]"
        parts = line.split('=>', 1)
        if len(parts) != 2:
            return

        resource = parts[0].strip()
        rest = parts[1].strip()

        # 提取 Active Locks 部分
        active_match = re.search(r'Active Locks:\s*\[(.*?)\]', rest)
        if active_match:
            active_str = active_match.group(1)
            if active_str:
                # 匹配每个 Lock
                for m in self.LOCK_RE.finditer(active_str):
                    lock = HeldLock(
                        trans_num=int(m.group(1)),
                        lock_type=m.group(2),
                        resource=m.group(3),
                    )
                    held_locks.setdefault(lock.trans_num, []).append(lock)

        # 提取 Queue 部分
        queue_match = re.search(r'Queue:\s*\[(.*?)\]', rest)
        if queue_match:
            queue_str = queue_match.group(1)
            waiting_list: list[WaitingRequest] = []
            if queue_str:
                for m in self.WAIT_RE.finditer(queue_str):
                    req = WaitingRequest(
                        trans_num=int(m.group(1)),
                        lock_type=m.group(2),
                        resource=m.group(3),
                    )
                    waiting_list.append(req)
            waiting[resource] = waiting_list


# =============================================================================
# WFGBuilder — 构造等待图
# =============================================================================


class WFGBuilder:
    """从 LockSnapshot 构造 WaitForGraph。"""

    # 锁冲突矩阵：conflicts[a][b] = True 表示 a 与 b 冲突
    # 仅包含非 NL 类型（NL 与任何类型兼容，永远不冲突）
    _CONFLICTS: dict[str, dict[str, bool]] = {
        'X':   {'X': True, 'S': True,  'SIX': True,  'IX': True,  'IS': True},
        'S':   {'X': True, 'S': False, 'SIX': True,  'IX': True,  'IS': False},
        'SIX': {'X': True, 'S': True,  'SIX': True,  'IX': True,  'IS': False},
        'IX':  {'X': True, 'S': True,  'SIX': True,  'IX': False, 'IS': False},
        'IS':  {'X': True, 'S': False, 'SIX': False, 'IX': False, 'IS': False},
    }

    @classmethod
    def _conflict(cls, type_a: str, type_b: str) -> bool:
        """检查两种锁类型是否冲突。NL 与任何类型不冲突。"""
        if type_a == 'NL' or type_b == 'NL':
            return False
        return cls._CONFLICTS.get(type_a, {}).get(type_b, False)

    def build(self, snapshot: LockSnapshot) -> WaitForGraph:
        """构造等待图。"""
        wfg = WaitForGraph()

        # 收集所有出现过的事务号
        all_trans = set(snapshot.held_locks.keys())
        for waiters in snapshot.waiting.values():
            for w in waiters:
                all_trans.add(w.trans_num)
        wfg.nodes = all_trans

        # 对每个资源的等待队列，等待者连持有者
        for resource, waiters in snapshot.waiting.items():
            # 找出该资源的持有者
            holders: list[HeldLock] = []
            for locks in snapshot.held_locks.values():
                for lock in locks:
                    if lock.resource == resource:
                        holders.append(lock)

            for waiter in waiters:
                for holder in holders:
                    if waiter.trans_num == holder.trans_num:
                        continue
                    # 只有锁类型冲突才加边
                    if self._conflict(waiter.lock_type, holder.lock_type):
                        wfg.edges.append((waiter.trans_num, holder.trans_num))

        return wfg


# =============================================================================
# CycleDetector — DFS 找环
# =============================================================================


class CycleDetector:
    """在 WFG 中检测有向环（死锁）。"""

    def detect(self, wfg: WaitForGraph) -> list[Cycle]:
        """DFS 三色标记找环。返回所有环。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = {node: WHITE for node in wfg.nodes}
        cycles: list[Cycle] = []

        # 构建邻接表
        adj: dict[int, list[int]] = {node: [] for node in wfg.nodes}
        for u, v in wfg.edges:
            adj.setdefault(u, []).append(v)

        def dfs(u: int, path: list[int]) -> None:
            color[u] = GRAY
            path.append(u)

            for v in adj.get(u, []):
                if color.get(v) == GRAY:
                    # 找到环
                    idx = path.index(v)
                    cycle_txns = path[idx:] + [v]
                    cycles.append(Cycle(transactions=cycle_txns))
                elif color.get(v) == WHITE:
                    dfs(v, path)

            path.pop()
            color[u] = BLACK

        for node in wfg.nodes:
            if color[node] == WHITE:
                dfs(node, [])

        return cycles


# =============================================================================
# VictimSelector — 策略模式
# =============================================================================


class VictimSelector(ABC):
    """Victim 选择策略接口。"""

    @abstractmethod
    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        """返回 (victim_trans_num, reason)。"""
        ...


class MinLocksSelector(VictimSelector):
    """回滚持有锁数量最少的事务。类 MySQL。"""

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        candidates = list(set(cycle.transactions))  # 去重
        lock_counts = {
            t: len(snapshot.held_locks.get(t, [])) for t in candidates
        }
        # 最少锁，平局选 transNum 最小
        victim = min(candidates, key=lambda t: (lock_counts[t], t))
        reason = (
            f"T{victim} holds {lock_counts[victim]} lock(s) — "
            f"fewest among cycle members [MinLocks]"
        )
        return victim, reason


class YoungestFirstSelector(VictimSelector):
    """回滚最晚开始的事务。类 CockroachDB。"""

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        candidates = list(set(cycle.transactions))
        # 最晚开始（时间戳最大），平局选 transNum 最大
        victim = max(
            candidates,
            key=lambda t: (snapshot.trans_times.get(t, 0), t),
        )
        start_time = snapshot.trans_times.get(victim, 0)
        reason = (
            f"T{victim} started at {start_time} — "
            f"youngest in cycle [YoungestFirst]"
        )
        return victim, reason


# =============================================================================
# RollbackExecutor — 执行 \kill
# =============================================================================


class RollbackExecutor:
    """通过 TCP 向 rookieDB 发送 \kill 命令。"""

    def __init__(self, host: str = "localhost", port: int = 18600):
        self.host = host
        self.port = port

    async def kill(self, trans_num: int) -> bool:
        """
        回滚指定事务。打开新连接执行 \kill 后关闭。
        返回 True 表示成功。
        """
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port
            )
            # 跳过欢迎 banner
            await self._flush(reader)

            # 发送 \kill 命令
            cmd = f"\\kill {trans_num}"
            writer.write((cmd + '\n').encode())
            await writer.drain()

            response = await self._read_line(reader, timeout=3.0)
            writer.close()
            await writer.wait_closed()

            if response and 'rolled back' in response.lower():
                return True
            return False
        except Exception as e:
            print(f"  [Rollback] kill T{trans_num} 失败: {e}")
            return False

    async def _flush(self, reader: asyncio.StreamReader) -> None:
        """读取并丢弃缓冲区中的残留数据（banner 等）。"""
        for _ in range(10):
            line = await self._read_line(reader, timeout=0.15)
            if line is None:
                break

    async def _read_line(
        self, reader: asyncio.StreamReader, timeout: float
    ) -> Optional[str]:
        """读一行，超时返回 None。"""
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            return line.decode().strip() if line else None
        except asyncio.TimeoutError:
            return None


# =============================================================================
# PollingMonitor — 主循环
# =============================================================================


class PollingMonitor:
    """asyncio 主循环：轮询、检测、选 victim、回滚。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 18600,
        interval: float = 0.5,
        selector: Optional[VictimSelector] = None,
    ):
        self.host = host
        self.port = port
        self.interval = interval
        self.selector = selector or MinLocksSelector()
        self.parser = LockParser()
        self.builder = WFGBuilder()
        self.detector = CycleDetector()
        self.executor = RollbackExecutor(host, port)

        # 统计
        self.cycle_num = 0
        self.deadlocks_detected = 0
        self.transactions_killed = 0

    async def run(self, stop_event: asyncio.Event) -> None:
        """主轮询循环。"""
        consecutive_failures = 0

        while not stop_event.is_set():
            self.cycle_num += 1

            # 1. 轮询锁状态
            raw_text = await self._poll()
            if raw_text is None:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(f"\n[Monitor] 连续 {consecutive_failures} 次轮询失败，退出")
                    return
                await asyncio.sleep(self.interval)
                continue
            consecutive_failures = 0

            # 2. 解析
            snapshot = self.parser.parse(raw_text)
            if snapshot is None:
                await asyncio.sleep(self.interval)
                continue

            # 3. 构造 WFG
            wfg = self.builder.build(snapshot)

            # 4. 检测环
            cycles = self.detector.detect(wfg)

            if not cycles:
                self._log_normal(snapshot)
                await asyncio.sleep(self.interval)
                continue

            # 5. 取第一个环，选 victim
            self.deadlocks_detected += 1
            cycle = cycles[0]
            victim, reason = self.selector.select(cycle, snapshot)

            # 6. 回滚
            success = await self.executor.kill(victim)
            if success:
                self.transactions_killed += 1

            # 7. 展开输出
            self._log_deadlock(cycle, wfg, snapshot, victim, reason, success)

            await asyncio.sleep(self.interval)

    async def _poll(self) -> Optional[str]:
        """向 rookieDB 发送 \alllocks，返回响应文本。"""
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port
            )
            # 跳过 banner
            await self._flush(reader)

            # 发送 \alllocks
            writer.write(b'\\alllocks\n')
            await writer.drain()

            # 读取响应
            lines = []
            while True:
                line = await self._read_line(reader, timeout=1.0)
                if line is None:
                    break
                if not line and lines:
                    # 空行可能表示响应结束
                    # 继续读一小段时间确认
                    continue
                lines.append(line)
                # transactionTimes 是最后一行有意义的内容
                if 'transactionTimes:' in line:
                    break

            writer.close()
            await writer.wait_closed()
            raw_text = '\n'.join(lines)
            # rookiedb 的 '=> ' 提示符无换行，可能跟第一条输出
            # 一起 flush。去掉第一行可能残留的 '=> ' 前缀。
            if raw_text.startswith('=> '):
                raw_text = raw_text[3:]
            return raw_text
        except Exception as e:
            print(f"  [Monitor] 轮询失败: {e}")
            return None

    async def _flush(self, reader: asyncio.StreamReader) -> None:
        """读取并丢弃缓冲区中的残留数据（banner + 初始 => 提示符）。

        rookiedb 的 '=> ' 提示符没有换行，readline 无法消费。
        最后用 read() 清掉残留的 '=> ' 字节。
        """
        for _ in range(10):
            line = await self._read_line(reader, timeout=0.15)
            if line is None:
                break
        # 消耗残留的 '=> ' 提示符（无换行，readline 读不到）
        try:
            await asyncio.wait_for(reader.read(256), timeout=0.15)
        except asyncio.TimeoutError:
            pass

    async def _read_line(
        self, reader: asyncio.StreamReader, timeout: float
    ) -> Optional[str]:
        """读一行，超时返回 None。"""
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            return line.decode().strip() if line else None
        except asyncio.TimeoutError:
            return None

    # ---- 输出 ----

    def _log_normal(self, snapshot: LockSnapshot) -> None:
        """常规轮询：一行摘要。"""
        active = len(snapshot.held_locks)
        waiting = sum(
            len(w) for w in snapshot.waiting.values()
        )
        print(f"[Cycle #{self.cycle_num}] {active} active, {waiting} waiting — clear")

    def _log_deadlock(
        self,
        cycle: Cycle,
        wfg: WaitForGraph,
        snapshot: LockSnapshot,
        victim: int,
        reason: str,
        success: bool,
    ) -> None:
        """死锁检测：展开输出。"""
        print(f"\n{'=' * 60}")
        print(f"[Cycle #{self.cycle_num}] DEADLOCK DETECTED")
        print(f"  Cycle: {' → '.join(f'T{t}' for t in cycle.transactions)}")
        print(f"  WFG: {len(wfg.nodes)} nodes, {len(wfg.edges)} edges")
        for u, v in wfg.edges:
            print(f"    T{u} → T{v}")

        # 环上各事务的详情
        for t in set(cycle.transactions):
            locks = snapshot.held_locks.get(t, [])
            waits: list[str] = []
            for resource, waiters in snapshot.waiting.items():
                for w in waiters:
                    if w.trans_num == t:
                        waits.append(f"{w.lock_type}({resource})")
            lock_str = ', '.join(
                f"{l.lock_type}({l.resource})" for l in locks
            )
            wait_str = ', '.join(waits) if waits else '—'
            age = snapshot.trans_times.get(t, 0)
            print(f"    T{t}: holds [{lock_str}], waits [{wait_str}], "
                  f"startTime={age}")

        print(f"\n  Victim: T{victim}")
        print(f"  Reason: {reason}")
        status = '✓' if success else '✗'
        print(f"  Rollback: {status}")
        print(f"{'=' * 60}\n")


# =============================================================================
# main()
# =============================================================================


async def main():
    """DDA 主入口。"""
    print("=" * 60)
    print("DDA — DB Deadlock Agent")
    print("=" * 60)
    print()

    # === 配置 ===
    host = "localhost"
    port = 18600

    # === 策略选择 ===
    # 阶段一：切换下面两行来对比 MinLocks vs YoungestFirst
    selector = MinLocksSelector()
    # selector = YoungestFirstSelector()

    strategy_name = selector.__class__.__name__.replace('Selector', '')
    print(f"策略: {strategy_name}")
    print(f"目标: {host}:{port}")
    print()

    # === 检查 rookieDB 是否可达 ===
    print("检查 rookieDB 连接...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3.0
        )
        writer.close()
        await writer.wait_closed()
        print("  ✓ rookieDB 在线\n")
    except Exception:
        print("  ✗ 无法连接 rookieDB。请先启动:")
        print("    java -cp target/classes edu.berkeley.cs186.database.cli.Server &")
        print()
        return

    # === 选择场景 ===
    from scenarios import two_table_deadlock

    scenario = two_table_deadlock
    scenario_name = getattr(scenario, '__name__', 'unknown')
    print(f"场景: {scenario_name}")

    # === 启动 ===
    monitor = PollingMonitor(
        host=host,
        port=port,
        interval=0.5,
        selector=selector,
    )
    stop_event = asyncio.Event()

    print("启动监控...\n")
    monitor_task = asyncio.create_task(monitor.run(stop_event))

    # 等一小会儿让 monitor 开始第一轮轮询
    await asyncio.sleep(0.3)

    # 跑场景
    try:
        result = await scenario(host, port)
        print(f"\n场景结果: {result}\n")
    except Exception as e:
        print(f"\n场景异常: {e}\n")

    # 再等几轮，确认死锁已解除
    await asyncio.sleep(2.0)

    # 停止监控
    stop_event.set()
    await monitor_task

    # === 汇总 ===
    print()
    print("=" * 60)
    print("运行汇总")
    print("=" * 60)
    print(f"  轮询周期: {monitor.cycle_num}")
    print(f"  检测到死锁: {monitor.deadlocks_detected}")
    print(f"  已回滚事务: {monitor.transactions_killed}")
    print(f"  策略: {strategy_name}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
