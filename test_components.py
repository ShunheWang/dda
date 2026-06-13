"""DDA 核心组件单元测试——不需要 rookiedb。

测试范围：LockParser, WFGBuilder, VictimSelector (MinLocks, YoungestFirst)
"""

import pytest
from dda.models import (
    HeldLock, LockSnapshot, WaitingRequest, WaitForGraph, Cycle,
)
from dda.parser import LockParser
from dda.wfg import WFGBuilder
from dda.detector import CycleDetector
from dda.selector import MinLocksSelector, YoungestFirstSelector

# =============================================================================
# Fixtures
# =============================================================================

# 两事务死锁的 \alllocks 输出（真实格式）
TWO_TXN_DEADLOCK_OUTPUT = """=== LockManager State ===
transactionLocks: {5=[T5: S(database/_metadata.indices/dda_b), T5: IS(database/_metadata.tables), T5: SIX(database/dda_b), T5: S(database/_metadata.tables/dda_a), T5: S(database/_metadata.tables/dda_b), T5: IS(database/_metadata.indices), T5: X(database/dda_b/60000000001), T5: IX(database)], 6=[T6: SIX(database/dda_a), T6: S(database/_metadata.indices/dda_a), T6: IS(database/_metadata.tables), T6: S(database/_metadata.tables/dda_a), T6: S(database/_metadata.tables/dda_b), T6: IS(database/_metadata.indices), T6: IX(database), T6: X(database/dda_a/50000000001)]}
resourceEntries:
  database/_metadata.indices/dda_b => Active Locks: [T5: S(database/_metadata.indices/dda_b)], Queue: []
  database/_metadata.tables => Active Locks: [T5: IS(database/_metadata.tables), T6: IS(database/_metadata.tables)], Queue: []
  database/dda_a => Active Locks: [T6: SIX(database/dda_a)], Queue: [Request for T5: S(database/dda_a) (releasing [])]
  database/_metadata.indices/dda_a => Active Locks: [T6: S(database/_metadata.indices/dda_a)], Queue: []
  database/dda_b => Active Locks: [T5: SIX(database/dda_b)], Queue: [Request for T6: S(database/dda_b) (releasing [])]
  database/dda_a/50000000001 => Active Locks: [T6: X(database/dda_a/50000000001)], Queue: []
  database/_metadata.tables/dda_a => Active Locks: [T6: S(database/_metadata.tables/dda_a)], Queue: []
  database/_metadata.tables/dda_b => Active Locks: [T5: S(database/_metadata.tables/dda_b)], Queue: []
  database/_metadata.indices => Active Locks: [T5: IS(database/_metadata.indices), T6: IS(database/_metadata.indices)], Queue: []
  database => Active Locks: [T5: IX(database), T6: IX(database)], Queue: []
  database/dda_b/60000000001 => Active Locks: [T5: X(database/dda_b/60000000001)], Queue: []
transactionTimes: {5=1718150400000, 6=1718150400100}"""

# 无死锁的输出
NO_DEADLOCK_OUTPUT = """=== LockManager State ===
transactionLocks: {3=[T3: IS(database/_metadata.tables), T3: S(database/_metadata.tables/tt), T3: IX(database), T3: SIX(database/tt), T3: X(database/tt/30000000001), T3: IS(database/_metadata.indices), T3: S(database/_metadata.indices/tt)]}
resourceEntries:
  database/_metadata.tables => Active Locks: [T3: IS(database/_metadata.tables)], Queue: []
  database => Active Locks: [T3: IX(database)], Queue: []
  database/tt/30000000001 => Active Locks: [T3: X(database/tt/30000000001)], Queue: []
  database/_metadata.indices/tt => Active Locks: [T3: S(database/_metadata.indices/tt)], Queue: []
  database/_metadata.tables/tt => Active Locks: [T3: S(database/_metadata.tables/tt)], Queue: []
  database/_metadata.indices => Active Locks: [T3: IS(database/_metadata.indices)], Queue: []
  database/tt => Active Locks: [T3: SIX(database/tt)], Queue: []
transactionTimes: {3=1781333996112}"""

# 空锁状态
EMPTY_OUTPUT = """=== LockManager State ===
transactionLocks: {}
resourceEntries:
transactionTimes: {}"""


# =============================================================================
# LockParser
# =============================================================================

class TestLockParser:
    def test_parse_deadlock_snapshot(self):
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        assert snapshot is not None

        # 验证事务
        assert 5 in snapshot.held_locks
        assert 6 in snapshot.held_locks
        assert len(snapshot.held_locks) == 2

        # 验证事务时间
        assert snapshot.trans_times[5] == 1718150400000
        assert snapshot.trans_times[6] == 1718150400100

        # 验证 raw_text
        assert snapshot.raw_text == TWO_TXN_DEADLOCK_OUTPUT

    def test_parse_waiting_queues(self):
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        assert snapshot is not None

        # T5 在等 dda_a
        dda_a_waiters = snapshot.waiting.get('database/dda_a', [])
        assert len(dda_a_waiters) == 1
        assert dda_a_waiters[0].trans_num == 5
        assert dda_a_waiters[0].lock_type == 'S'

        # T6 在等 dda_b
        dda_b_waiters = snapshot.waiting.get('database/dda_b', [])
        assert len(dda_b_waiters) == 1
        assert dda_b_waiters[0].trans_num == 6
        assert dda_b_waiters[0].lock_type == 'S'

    def test_parse_held_locks(self):
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        assert snapshot is not None

        # T5 持有 SIX on dda_b, X on 行
        t5_locks = snapshot.held_locks[5]
        t5_resources = [l.resource for l in t5_locks]
        assert 'database/dda_b' in t5_resources
        assert 'database/dda_b/60000000001' in t5_resources

        # T6 持有 SIX on dda_a, X on 行
        t6_locks = snapshot.held_locks[6]
        t6_resources = [l.resource for l in t6_locks]
        assert 'database/dda_a' in t6_resources
        assert 'database/dda_a/50000000001' in t6_resources

    def test_parse_no_deadlock(self):
        snapshot = LockParser().parse(NO_DEADLOCK_OUTPUT)
        assert snapshot is not None
        assert 3 in snapshot.held_locks
        # 没有 waiting
        assert all(len(w) == 0 for w in snapshot.waiting.values())

    def test_parse_empty(self):
        snapshot = LockParser().parse(EMPTY_OUTPUT)
        assert snapshot is not None
        assert len(snapshot.held_locks) == 0
        assert len(snapshot.waiting) == 0
        assert len(snapshot.trans_times) == 0

    def test_parse_invalid(self):
        result = LockParser().parse("garbage text not matching format")
        # 不应该抛异常——返回可能为 None 或空 snapshot
        # 设计原则：解析失败不崩溃，调用方跳过本轮
        if result is not None:
            assert len(result.held_locks) == 0
            assert len(result.waiting) == 0


# =============================================================================
# WFGBuilder
# =============================================================================

class TestWFGBuilder:
    def test_build_from_deadlock(self):
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        wfg = WFGBuilder().build(snapshot)

        assert 5 in wfg.nodes
        assert 6 in wfg.nodes

        # T5 等 T6 (T5 waiter of dda_a, T6 holder of dda_a)
        assert (5, 6) in wfg.edges
        # T6 等 T5 (T6 waiter of dda_b, T5 holder of dda_b)
        assert (6, 5) in wfg.edges

    def test_no_self_edges(self):
        """事务等待者不连自己"""
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        wfg = WFGBuilder().build(snapshot)
        assert (5, 5) not in wfg.edges
        assert (6, 6) not in wfg.edges

    def test_no_deadlock_graph(self):
        snapshot = LockParser().parse(NO_DEADLOCK_OUTPUT)
        wfg = WFGBuilder().build(snapshot)
        assert len(wfg.edges) == 0  # 无等待者

    def test_empty_graph(self):
        snapshot = LockParser().parse(EMPTY_OUTPUT)
        wfg = WFGBuilder().build(snapshot)
        assert len(wfg.nodes) == 0
        assert len(wfg.edges) == 0

    def test_lock_conflict_matrix(self):
        """验证锁冲突矩阵的精确性"""
        # X 与 IS 冲突（IS 请求等 X 持有者）
        assert WFGBuilder._conflict('IS', 'X') is True
        # SIX 与 S 冲突
        assert WFGBuilder._conflict('S', 'SIX') is True
        # IS 与 IX 兼容
        assert WFGBuilder._conflict('IX', 'IS') is False
        # NL 永远不冲突
        assert WFGBuilder._conflict('X', 'NL') is False
        assert WFGBuilder._conflict('NL', 'X') is False


# =============================================================================
# CycleDetector
# =============================================================================

class TestCycleDetector:
    def test_detect_simple_cycle(self):
        wfg = WaitForGraph(
            nodes={1, 2},
            edges=[(1, 2), (2, 1)],
        )
        cycles = CycleDetector().detect(wfg)
        assert len(cycles) >= 1
        # T1 → T2 → T1
        assert 1 in cycles[0].transactions
        assert 2 in cycles[0].transactions

    def test_no_cycle(self):
        wfg = WaitForGraph(
            nodes={1, 2},
            edges=[(1, 2)],  # 单向等，不成环
        )
        cycles = CycleDetector().detect(wfg)
        assert len(cycles) == 0

    def test_self_loop(self):
        """自环：事务等自己"""
        wfg = WaitForGraph(
            nodes={1},
            edges=[(1, 1)],
        )
        cycles = CycleDetector().detect(wfg)
        assert len(cycles) == 1
        assert cycles[0].transactions == [1, 1]

    def test_three_node_cycle(self):
        wfg = WaitForGraph(
            nodes={1, 2, 3},
            edges=[(1, 2), (2, 3), (3, 1)],
        )
        cycles = CycleDetector().detect(wfg)
        assert len(cycles) == 1

    def test_empty_graph(self):
        wfg = WaitForGraph()
        cycles = CycleDetector().detect(wfg)
        assert len(cycles) == 0


# =============================================================================
# VictimSelector
# =============================================================================

class TestMinLocksSelector:
    def test_selects_fewest_locks(self):
        """T5 有 8 个锁，T6 有 8 个锁——平局选 transNum 最小"""
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        cycle = Cycle(transactions=[5, 6, 5])
        victim, reason = MinLocksSelector().select(cycle, snapshot)

        # 都是 8 个锁，平局选 transNum 较小者 (5)
        assert victim == 5
        assert 'fewest' in reason.lower()

    def test_selects_fewest_locks_clear_winner(self):
        """T1 持有 1 锁，T2 持有 3 锁——选 T1"""
        snapshot = LockSnapshot(
            held_locks={
                1: [HeldLock(1, 'X', 'db://a')],
                2: [HeldLock(2, 'X', 'db://b'),
                    HeldLock(2, 'S', 'db://c'),
                    HeldLock(2, 'IX', 'db://d')],
            },
            waiting={},
            trans_times={1: 100, 2: 200},
            raw_text='',
        )
        cycle = Cycle(transactions=[1, 2, 1])
        victim, _ = MinLocksSelector().select(cycle, snapshot)
        assert victim == 1

    def test_tiebreak_by_transnum(self):
        """锁数相同选 transNum 最小的"""
        snapshot = LockSnapshot(
            held_locks={
                10: [HeldLock(10, 'X', 'db://a')],
                20: [HeldLock(20, 'X', 'db://b')],
            },
            waiting={},
            trans_times={10: 100, 20: 200},
            raw_text='',
        )
        cycle = Cycle(transactions=[10, 20, 10])
        victim, _ = MinLocksSelector().select(cycle, snapshot)
        assert victim == 10


class TestYoungestFirstSelector:
    def test_selects_youngest(self):
        """T6 的 startTime 比 T5 晚——选 T6"""
        snapshot = LockParser().parse(TWO_TXN_DEADLOCK_OUTPUT)
        cycle = Cycle(transactions=[5, 6, 5])
        victim, reason = YoungestFirstSelector().select(cycle, snapshot)

        # T5: 1718150400000, T6: 1718150400100 (晚 100ms)
        assert victim == 6
        assert 'youngest' in reason.lower()

    def test_tiebreak_by_transnum(self):
        """startTime 相同选 transNum 最大的"""
        snapshot = LockSnapshot(
            held_locks={
                10: [HeldLock(10, 'X', 'db://a')],
                20: [HeldLock(20, 'X', 'db://b')],
            },
            waiting={},
            trans_times={10: 100, 20: 100},  # 同时启动
            raw_text='',
        )
        cycle = Cycle(transactions=[10, 20, 10])
        victim, _ = YoungestFirstSelector().select(cycle, snapshot)
        assert victim == 20

    def test_missing_time_defaults_to_zero(self):
        """事务无 startTime 时默认 0，最老"""
        snapshot = LockSnapshot(
            held_locks={
                1: [HeldLock(1, 'X', 'db://a')],
                2: [HeldLock(2, 'X', 'db://b')],
            },
            waiting={},
            trans_times={1: 1000},  # T2 无时间
            raw_text='',
        )
        cycle = Cycle(transactions=[1, 2, 1])
        victim, _ = YoungestFirstSelector().select(cycle, snapshot)
        # T2 startTime 默认 0，比 T1 (1000) 老，选 T1
        assert victim == 1


# =============================================================================
# 集成：Parser → WFG → Cycle → Victim 全链路
# =============================================================================

class TestIntegration:
    def test_full_pipeline_minlocks(self):
        """从 \alllocks 文本到 victim 选择，全链路验证"""
        parser = LockParser()
        builder = WFGBuilder()
        detector = CycleDetector()
        selector = MinLocksSelector()

        snapshot = parser.parse(TWO_TXN_DEADLOCK_OUTPUT)
        assert snapshot is not None

        wfg = builder.build(snapshot)
        cycles = detector.detect(wfg)
        assert len(cycles) == 1

        victim, reason = selector.select(cycles[0], snapshot)
        assert victim in (5, 6)
        assert len(reason) > 0

    def test_full_pipeline_youngest(self):
        parser = LockParser()
        builder = WFGBuilder()
        detector = CycleDetector()
        selector = YoungestFirstSelector()

        snapshot = parser.parse(TWO_TXN_DEADLOCK_OUTPUT)
        wfg = builder.build(snapshot)
        cycles = detector.detect(wfg)
        victim, reason = selector.select(cycles[0], snapshot)

        assert victim == 6  # T6 更年轻
