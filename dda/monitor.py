"""PollingMonitor — asyncio 主循环：轮询、检测、选 victim、回滚。"""

import asyncio
from typing import Optional

from dda.connection import flush, read_line
from dda.models import Cycle, LockSnapshot, WaitForGraph
from dda.parser import LockParser
from dda.wfg import WFGBuilder
from dda.detector import CycleDetector
from dda.selector import MinLocksSelector, VictimSelector
from dda.executor import RollbackExecutor


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

            # 7. 输出
            self._log_deadlock(cycle, wfg, snapshot, victim, reason, success)

            await asyncio.sleep(self.interval)

    async def _poll(self) -> Optional[str]:
        """向 rookieDB 发送 \\alllocks，返回响应文本。"""
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port
            )
            await flush(reader)

            writer.write(b'\\alllocks\n')
            await writer.drain()

            lines = []
            while True:
                line = await read_line(reader, timeout=1.0)
                if line is None:
                    break
                if not line and lines:
                    continue
                lines.append(line)
                if 'transactionTimes:' in line:
                    break

            writer.close()
            await writer.wait_closed()
            raw_text = '\n'.join(lines)
            if raw_text.startswith('=> '):
                raw_text = raw_text[3:]
            return raw_text
        except Exception as e:
            print(f"  [Monitor] 轮询失败: {e}")
            return None

    # ---- 输出 ----

    def _log_normal(self, snapshot: LockSnapshot) -> None:
        """常规轮询：一行摘要。"""
        active = len(snapshot.held_locks)
        waiting = sum(len(w) for w in snapshot.waiting.values())
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
