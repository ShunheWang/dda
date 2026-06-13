"""\\alllocks 输出解析 → LockSnapshot。"""

import re
from typing import Optional

from dda.models import HeldLock, LockSnapshot, WaitingRequest


class LockParser:
    """将 \\alllocks 原始文本解析为 LockSnapshot。"""

    # 匹配 Lock: T1: X(db://tableA)
    LOCK_RE = re.compile(r'T(\d+):\s*(\w+)\((.+?)\)')

    # 匹配 LockRequest: Request for T1: X(db://tableA) (releasing [...])
    WAIT_RE = re.compile(r'Request for T(\d+):\s*(\w+)\((.+?)\)')

    # 匹配 transactionTimes: {1=123, 2=456}
    TIME_RE = re.compile(r'(\d+)=(\d+)')

    def parse(self, raw_text: str) -> Optional[LockSnapshot]:
        """解析 \\alllocks 输出。失败返回 None。"""
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

                # resourceEntries 段结束
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
