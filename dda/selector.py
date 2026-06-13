"""Victim 选择策略 —— Strategy 模式。"""

from abc import ABC, abstractmethod

from dda.models import Cycle, LockSnapshot


class VictimSelector(ABC):
    """Victim 选择策略接口。"""

    @abstractmethod
    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        """返回 (victim_trans_num, reason)。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称，用于输出。"""
        ...


class MinLocksSelector(VictimSelector):
    """回滚持有锁数量最少的事务。类 MySQL (InnoDB)。"""

    @property
    def name(self) -> str:
        return "MinLocks"

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        candidates = list(set(cycle.transactions))
        lock_counts = {
            t: len(snapshot.held_locks.get(t, [])) for t in candidates
        }
        victim = min(candidates, key=lambda t: (lock_counts[t], t))
        reason = (
            f"T{victim} holds {lock_counts[victim]} lock(s) — "
            f"fewest among cycle members [MinLocks]"
        )
        return victim, reason


class YoungestFirstSelector(VictimSelector):
    """回滚最晚开始的事务。类 CockroachDB。"""

    @property
    def name(self) -> str:
        return "YoungestFirst"

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        candidates = list(set(cycle.transactions))
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


class CycleTriggerSelector(VictimSelector):
    """回滚环上在等待队列中位置最靠后的事务。

    思路：Percona/MariaDB 的"最后请求者优先"——
    打破最新形成的等待关系，与锁等待的自然时序一致。
    """

    @property
    def name(self) -> str:
        return "CycleTrigger"

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        candidates = list(set(cycle.transactions))

        # 计算每个候选事务在等待队列中的最大位置
        def queue_position(t: int) -> int:
            max_pos = -1
            for resource, waiters in snapshot.waiting.items():
                for i, w in enumerate(waiters):
                    if w.trans_num == t:
                        max_pos = max(max_pos, i)
            return max_pos

        # 选队列位置最靠后的，平局选 transNum 最大
        victim = max(candidates, key=lambda t: (queue_position(t), t))
        reason = (
            f"T{victim} is last in request queue — "
            f"cycle trigger [CycleTrigger]"
        )
        return victim, reason


class LLMSelector(VictimSelector):
    """LLM 语义判断选 victim（阶段二接入点）。"""

    @property
    def name(self) -> str:
        return "LLM"

    def __init__(self, client=None, fallback: VictimSelector | None = None):
        self.client = client
        self.fallback = fallback or MinLocksSelector()

    def select(self, cycle: Cycle, snapshot: LockSnapshot) -> tuple[int, str]:
        # 阶段二实现：调用 Anthropic API
        # 失败时 fallback.select(cycle, snapshot)
        return self.fallback.select(cycle, snapshot)
