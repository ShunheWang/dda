"""Wait-for Graph 构造。"""

from dda.models import HeldLock, LockSnapshot, WaitForGraph


class WFGBuilder:
    """从 LockSnapshot 构造 WaitForGraph。"""

    # 锁冲突矩阵：conflicts[a][b] = True 表示 a 与 b 冲突
    _CONFLICTS: dict[str, dict[str, bool]] = {
        'X':   {'X': True, 'S': True,  'SIX': True,  'IX': True,  'IS': True},
        'S':   {'X': True, 'S': False, 'SIX': True,  'IX': True,  'IS': False},
        'SIX': {'X': True, 'S': True,  'SIX': True,  'IX': True,  'IS': False},
        'IX':  {'X': True, 'S': True,  'SIX': True,  'IX': False, 'IS': False},
        'IS':  {'X': True, 'S': False, 'SIX': False, 'IX': False, 'IS': False},
    }

    @staticmethod
    def _conflict(type_a: str, type_b: str) -> bool:
        """检查两种锁类型是否冲突。NL 与任何类型不冲突。"""
        if type_a == 'NL' or type_b == 'NL':
            return False
        return WFGBuilder._CONFLICTS.get(type_a, {}).get(type_b, False)

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
            holders: list[HeldLock] = []
            for locks in snapshot.held_locks.values():
                for lock in locks:
                    if lock.resource == resource:
                        holders.append(lock)

            for waiter in waiters:
                for holder in holders:
                    if waiter.trans_num == holder.trans_num:
                        continue
                    if self._conflict(waiter.lock_type, holder.lock_type):
                        wfg.edges.append((waiter.trans_num, holder.trans_num))

        return wfg
