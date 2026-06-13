"""DDA 数据结构。"""

from dataclasses import dataclass, field


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
    """\\alllocks 输出解析后的锁状态快照。"""
    held_locks: dict[int, list[HeldLock]]        # transNum → 持有的锁
    waiting: dict[str, list[WaitingRequest]]      # resource → 等待队列
    trans_times: dict[int, int]                   # transNum → startTime (epoch ms)
    raw_text: str                                 # 原始输出文本


@dataclass
class WaitForGraph:
    """等待图。边 (u, v) 表示 u 在等待 v 释放锁。"""
    nodes: set[int] = field(default_factory=set)
    edges: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Cycle:
    """WFG 中的一个有向环。"""
    transactions: list[int]  # 环上事务序列，首尾相同
