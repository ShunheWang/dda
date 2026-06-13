"""DDA 集成测试——自动启动/关闭 rookieDB。

测试范围：死锁检测全链路（真实 TCP 通信 + DDA 监控 + 回滚）

运行方式：
    python3 -m pytest test_integration.py -v

rookieDB 路径默认 ../cs186/berkeley-sp26-rookiedb，可通过
ROOKIEDB_HOME 环境变量覆盖。
"""

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from dda_basic import (
    PollingMonitor, MinLocksSelector, YoungestFirstSelector
)

ROOKIEDB_HOME = Path(os.environ.get(
    'ROOKIEDB_HOME',
    os.path.expanduser('~/melbourne/cs186/berkeley-sp26-rookiedb'),
))
ROOKIEDB_PORT = 18600


# =============================================================================
# rookieDB 生命周期
# =============================================================================

def _rookiedb_is_running() -> bool:
    """检查 rookieDB 是否在监听端口。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(('localhost', ROOKIEDB_PORT))
        s.close()
        return True
    except Exception:
        return False


def _kill_rookiedb():
    """杀掉已运行的 rookieDB。"""
    try:
        pid = subprocess.run(
            ['lsof', '-t', f'-i:{ROOKIEDB_PORT}'],
            capture_output=True, text=True,
        ).stdout.strip()
        if pid:
            os.kill(int(pid), signal.SIGTERM)
            time.sleep(0.5)
    except Exception:
        pass


def _start_rookiedb():
    """启动 rookieDB Server，等待就绪。"""
    # 清数据
    demo_dir = ROOKIEDB_HOME / 'demo'
    if demo_dir.exists():
        import shutil
        shutil.rmtree(demo_dir)

    subprocess.Popen(
        ['java', '-cp', 'target/classes',
         'edu.berkeley.cs186.database.cli.Server'],
        cwd=str(ROOKIEDB_HOME),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待就绪
    deadline = time.time() + 30
    while time.time() < deadline:
        if _rookiedb_is_running():
            return
        time.sleep(0.5)
    raise RuntimeError('rookieDB 启动超时')


@pytest.fixture(scope='session')
def rookiedb():
    """Session 级 fixture：启动 rookieDB，所有集成测试结束后关闭。"""
    _kill_rookiedb()
    _start_rookiedb()
    yield
    _kill_rookiedb()


# =============================================================================
# TCP 通信辅助
# =============================================================================

async def _connect():
    """连接 rookieDB，跳过 banner + 初始提示符。"""
    reader, writer = await asyncio.open_connection('localhost', ROOKIEDB_PORT)
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
    # metacommand（\ 开头）不加分号
    if not sql.startswith('\\') and not sql.rstrip().endswith(';'):
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


def _is_error(resp: str) -> bool:
    lower = resp.lower()
    return any(kw in lower for kw in [
        'error', 'rollback', 'exception', 'cannot commit',
        'not in running state', 'failed',
    ])


# =============================================================================
# 表管理
# =============================================================================

TABLE_A = 'dda_test_a'
TABLE_B = 'dda_test_b'


async def _setup_table(name: str):
    r, w = await _connect()
    try:
        await _execute(r, w, f'CREATE TABLE {name} (id INT, val INT)')
        await _execute(r, w, f"INSERT INTO {name} VALUES (1, 100)")
        await _execute(r, w, f"INSERT INTO {name} VALUES (2, 200)")
    finally:
        w.close()
        await w.wait_closed()


async def _drop_table(name: str):
    r, w = await _connect()
    try:
        await _execute(r, w, f'DROP TABLE {name}', timeout=1.0)
    except Exception:
        pass
    finally:
        w.close()
        await w.wait_closed()


async def _transaction(first_update: str, second_update: str, pause: float) -> dict:
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


def _run_scenario(selector) -> dict:
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


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def _setup_tables(rookiedb):
    """每个测试前后清理表。"""
    asyncio.run(_drop_table(TABLE_A))
    asyncio.run(_drop_table(TABLE_B))
    asyncio.run(_setup_table(TABLE_A))
    asyncio.run(_setup_table(TABLE_B))
    yield
    asyncio.run(_drop_table(TABLE_A))
    asyncio.run(_drop_table(TABLE_B))


# =============================================================================
# 测试
# =============================================================================

class TestDeadlockDetection:
    """死锁检测全链路。"""

    def test_minlocks_detects_and_resolves_deadlock(self):
        result = _run_scenario(MinLocksSelector())

        assert result['deadlocks_detected'] == 1
        assert result['transactions_killed'] == 1
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
        r1 = _run_scenario(MinLocksSelector())
        r2 = _run_scenario(YoungestFirstSelector())

        assert r1['deadlocks_detected'] == 1
        assert r2['deadlocks_detected'] == 1

    def test_survivor_commits_killed_gets_error(self):
        result = _run_scenario(MinLocksSelector())
        assert result['deadlocks_detected'] == 1

        assert (result['t1_killed'] and not result['t1_committed']) or \
               (result['t2_killed'] and not result['t2_committed'])

    def test_no_residual_locks(self):
        result = _run_scenario(MinLocksSelector())
        assert result['deadlocks_detected'] == 1

        async def check():
            r, w = await _connect()
            try:
                resp = await _execute(r, w, '\\alllocks', timeout=2.0)
                return 'transactionLocks: {}' in resp
            finally:
                w.close()
                await w.wait_closed()

        assert asyncio.run(check()), "死锁解除后不应有残留锁"
