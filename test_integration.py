"""DDA 集成测试——需要 rookieDB 在 localhost:18600 运行。

测试范围：死锁检测全链路（真实 TCP 通信 + DDA 监控 + 回滚）
"""

import asyncio
import pytest
from dda_basic import (
    PollingMonitor, MinLocksSelector, YoungestFirstSelector
)


# =============================================================================
# 辅助函数
# =============================================================================

async def _connect():
    """连接 rookieDB，跳过 banner + 初始提示符。"""
    reader, writer = await asyncio.open_connection('localhost', 18600)
    for _ in range(10):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=0.2)
            if not line:
                break
        except asyncio.TimeoutError:
            break
    try:
        await asyncio.wait_for(reader.read(256), timeout=0.2)
    except asyncio.TimeoutError:
        pass
    return reader, writer


async def _execute(reader, writer, sql: str, timeout: float = 5.0) -> str:
    """发送 SQL 并读取响应。"""
    if not sql.rstrip().endswith(';'):
        sql = sql.rstrip() + ';'
    writer.write((sql + '\n').encode())
    await writer.drain()

    first = await _readline(reader, timeout)
    if first is None:
        return ''
    cleaned = first.strip()
    while cleaned.startswith('=> '):
        cleaned = cleaned[3:]
    cleaned = cleaned.strip()

    more = []
    while True:
        line = await _readline(reader, 0.3)
        if line is None or line.strip() == '=>':
            break
        more.append(line)

    if more:
        return cleaned + '\n' + '\n'.join(more)
    return cleaned


async def _readline(reader, timeout: float):
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return line.decode().strip() if line else None
    except asyncio.TimeoutError:
        return None


async def _setup_table(name: str):
    """创建测试表并插入数据。已存在则跳过。"""
    r, w = await _connect()
    try:
        resp = await _execute(r, w, f'CREATE TABLE {name} (id INT, val INT)')
        # 忽略 "already exists" 错误
        await _execute(r, w, f"INSERT INTO {name} VALUES (1, 100)")
        await _execute(r, w, f"INSERT INTO {name} VALUES (2, 200)")
    finally:
        w.close()
        await w.wait_closed()


async def _drop_table(name: str):
    """删除测试表（尽力而为）。"""
    r, w = await _connect()
    try:
        await _execute(r, w, f'DROP TABLE {name}', timeout=1.0)
    except Exception:
        pass
    finally:
        w.close()
        await w.wait_closed()


def _is_error(resp: str) -> bool:
    lower = resp.lower()
    return any(kw in lower for kw in [
        'error', 'rollback', 'exception', 'cannot commit',
        'not in running state', 'failed',
    ])


async def _transaction(first_update: str, second_update: str, pause: float) -> dict:
    """执行单个事务：BEGIN → UPDATE1 → pause → UPDATE2 → COMMIT。"""
    result = {'committed': False, 'killed': False, 'error': None}
    r, w = await _connect()
    try:
        await _execute(r, w, 'BEGIN')
        await _execute(r, w, first_update)
        await asyncio.sleep(pause)

        resp = await _execute(r, w, second_update, timeout=30.0)
        if _is_error(resp):
            result['killed'] = True
            result['error'] = resp
            return result

        resp = await _execute(r, w, 'COMMIT')
        if _is_error(resp):
            result['killed'] = True
            result['error'] = resp
        else:
            result['committed'] = True
    finally:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
    return result


# =============================================================================
# 测试
# =============================================================================

TABLE_A = 'dda_test_a'
TABLE_B = 'dda_test_b'


@pytest.fixture(autouse=True)
def setup_teardown():
    """每个测试前后清理表。"""
    asyncio.run(_drop_table(TABLE_A))
    asyncio.run(_drop_table(TABLE_B))
    asyncio.run(_setup_table(TABLE_A))
    asyncio.run(_setup_table(TABLE_B))
    yield
    asyncio.run(_drop_table(TABLE_A))
    asyncio.run(_drop_table(TABLE_B))


def _run_scenario(selector) -> dict:
    """以指定策略跑一次两事务死锁场景，返回结果。"""
    monitor = PollingMonitor(selector=selector, interval=0.3)
    stop_event = asyncio.Event()

    async def _run():
        monitor_task = asyncio.create_task(monitor.run(stop_event))
        await asyncio.sleep(0.2)

        t1, t2 = await asyncio.gather(
            _transaction(
                f'UPDATE {TABLE_A} SET val = 10 WHERE id = 1',
                f'UPDATE {TABLE_B} SET val = 11 WHERE id = 1',
                pause=0.6,
            ),
            _transaction(
                f'UPDATE {TABLE_B} SET val = 20 WHERE id = 1',
                f'UPDATE {TABLE_A} SET val = 21 WHERE id = 1',
                pause=0.2,
            ),
        )

        await asyncio.sleep(1.5)
        stop_event.set()
        await monitor_task

        return {
            'deadlocks_detected': monitor.deadlocks_detected,
            'transactions_killed': monitor.transactions_killed,
            't1_committed': t1['committed'],
            't2_committed': t2['committed'],
            't1_killed': t1['killed'],
            't2_killed': t2['killed'],
        }

    return asyncio.run(_run())


class TestDeadlockDetection:
    """死锁检测全链路——需 rookieDB 运行。"""

    def test_minlocks_detects_and_resolves_deadlock(self):
        result = _run_scenario(MinLocksSelector())

        assert result['deadlocks_detected'] == 1
        assert result['transactions_killed'] == 1

        # 必须有一个存活、一个被杀
        assert result['t1_committed'] != result['t2_committed']
        killed = 't1' if result['t1_killed'] else 't2'
        survivor = 't2' if result['t1_killed'] else 't1'
        assert result[f'{survivor}_committed'] is True
        assert result[f'{killed}_killed'] is True

    def test_youngest_detects_and_resolves_deadlock(self):
        result = _run_scenario(YoungestFirstSelector())

        assert result['deadlocks_detected'] == 1
        assert result['transactions_killed'] == 1

        assert result['t1_committed'] != result['t2_committed']

    def test_strategies_may_differ(self):
        """不同策略可能选不同 victim——不要求不同，但两个都要跑通。"""
        r1 = _run_scenario(MinLocksSelector())
        r2 = _run_scenario(YoungestFirstSelector())

        # 两个策略都应该正常运行
        assert r1['deadlocks_detected'] == 1
        assert r2['deadlocks_detected'] == 1

    def test_survivor_commits_killed_gets_error(self):
        """被 kill 的事务必须收到错误信息。"""
        result = _run_scenario(MinLocksSelector())
        assert result['deadlocks_detected'] == 1

        # 至少一个被 kill 且 committed=False
        assert (result['t1_killed'] and not result['t1_committed']) or \
               (result['t2_killed'] and not result['t2_committed'])

    def test_no_residual_locks(self):
        """死锁解除后锁全部释放。"""
        result = _run_scenario(MinLocksSelector())
        assert result['deadlocks_detected'] == 1

        # 场景结束后检查锁状态
        async def check():
            r, w = await _connect()
            try:
                resp = await _execute(r, w, '\\alllocks', timeout=2.0)
                # transactionLocks 应为空
                return 'transactionLocks: {}' in resp
            finally:
                w.close()
                await w.wait_closed()

        assert asyncio.run(check()), "死锁解除后应有残留锁"
