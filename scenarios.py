#!/usr/bin/env python3
"""
DDA 死锁场景编排
=================

每个 scenario 是一个 async 函数，接受 (host, port)，返回执行结果摘要。

约定：
- 场景负责创建测试表、插入数据
- 场景负责启动并发事务
- 事务在独立 TCP 连接上执行
- 被 kill 的事务捕获错误并记录
"""

import asyncio
from typing import Optional


# =============================================================================
# TCP 通信辅助
# =============================================================================


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """连接到 rookieDB，跳过欢迎 banner + 初始 '=> ' 提示符。"""
    reader, writer = await asyncio.open_connection(host, port)
    # 跳过 banner 行
    for _ in range(10):
        line = await _read_line(reader, timeout=0.2)
        if line is None:
            break
    # rookiedb '=> ' 提示符无换行，readline 读不到，用 read() 清掉
    try:
        await asyncio.wait_for(reader.read(256), timeout=0.2)
    except asyncio.TimeoutError:
        pass
    return reader, writer


async def _read_line(reader: asyncio.StreamReader, timeout: float) -> Optional[str]:
    """读一行，超时返回 None。"""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return line.decode().strip() if line else None
    except asyncio.TimeoutError:
        return None


def _is_error(resp: str) -> bool:
    """判断 rookiedb 响应是否为错误（事务被 kill、SQL 失败等）。"""
    lower = resp.lower()
    return any(kw in lower for kw in [
        'error', 'rollback', 'exception', 'cannot commit',
        'not in running state', 'failed',
    ])


async def _execute(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                   sql: str, timeout: float = 10.0) -> str:
    """发送 SQL，读取并返回响应。

    rookiedb 的 PrintStream(autoFlush=true) 会在每次 println 时
    flush。但 '=> ' 提示符无换行，不会独立 flush——它会跟下一条
    命令的输出一起 flush，导致响应行开头有 '=> ' 前缀。
    """
    if not sql.rstrip().endswith(';'):
        sql = sql.rstrip() + ';'
    writer.write((sql + '\n').encode())
    await writer.drain()

    # 读第一行（含前置的 => 提示符 + 实际输出）
    first_line = await _read_line(reader, timeout=timeout)
    if first_line is None:
        return ''

    # 去掉开头的 '=> ' 前缀（可能有多个堆积）
    cleaned = first_line.strip()
    while cleaned.startswith('=> '):
        cleaned = cleaned[3:]
    cleaned = cleaned.strip()

    # 读后续行（错误 / 堆栈跟踪可能是多行的）
    # 用短超时——正常情况没有后续行，提示符无换行会触发超时
    more_lines: list[str] = []
    while True:
        line = await _read_line(reader, timeout=0.3)
        if line is None or line.strip() == '=>':
            break
        more_lines.append(line)

    if more_lines:
        return cleaned + '\n' + '\n'.join(more_lines)
    return cleaned


async def _setup_database(host: str, port: int) -> bool:
    """创建测试表并插入初始数据。"""
    try:
        reader, writer = await _connect(host, port)

        # 创建表（用 INT 而非 VARCHAR——rookieDB 对字符串字面量 INSERT 有限制）
        for sql in [
            'CREATE TABLE dda_a (id INT, val INT)',
            'CREATE TABLE dda_b (id INT, val INT)',
        ]:
            resp = await _execute(reader, writer, sql)
            # "SUCCESS" 或表已存在则报错，都继续
            if 'error' in resp.lower() or 'already exists' in resp.lower():
                print(f"  [Setup] {sql} → {resp}")

        # 插入数据
        for sql in [
            "INSERT INTO dda_a VALUES (1, 100)",
            "INSERT INTO dda_b VALUES (1, 200)",
        ]:
            resp = await _execute(reader, writer, sql)
            if 'error' in resp.lower():
                print(f"  [Setup] {sql} → {resp}")

        writer.close()
        await writer.wait_closed()
        return True
    except Exception as e:
        print(f"  [Setup] 失败: {e}")
        return False


# =============================================================================
# 并发事务模板
# =============================================================================


async def _transaction(
    host: str,
    port: int,
    label: str,
    first_update: str,
    second_update: str,
    pause_before_second: float,
) -> dict:
    """
    执行一个事务。

    1. BEGIN
    2. 执行 first_update（获取第一个锁）
    3. 等待 pause_before_second（让另一个事务获取锁）
    4. 执行 second_update（尝试获取第二个锁，可能阻塞/死锁）
    5. COMMIT（如果未被 kill）

    返回: {"label": ..., "committed": bool, "killed": bool, "error": str|None}
    """
    result = {
        "label": label,
        "committed": False,
        "killed": False,
        "error": None,
    }
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None

    try:
        reader, writer = await _connect(host, port)

        # BEGIN
        resp = await _execute(reader, writer, 'BEGIN')
        print(f"  [{label}] BEGIN → {resp}")

        # 第一步 UPDATE（获取第一个锁）
        resp = await _execute(reader, writer, first_update)
        print(f"  [{label}] {first_update} → {resp}")

        # 暂停，让另一个事务也获取它的第一个锁
        await asyncio.sleep(pause_before_second)

        # 第二步 UPDATE（可能阻塞，等待另一个事务释放锁）
        # 如果 DDA 在等待期间 kill 了本事务，readline 会返回错误
        print(f"  [{label}] {second_update} → 等待锁...")
        resp = await _execute(reader, writer, second_update, timeout=30.0)
        print(f"  [{label}] {second_update} → {resp}")

        # 成功获取锁 → COMMIT
        if _is_error(resp):
            result["killed"] = True
            result["error"] = resp
        else:
            resp = await _execute(reader, writer, 'COMMIT')
            print(f"  [{label}] COMMIT → {resp}")
            if _is_error(resp):
                result["killed"] = True
                result["error"] = resp
            else:
                result["committed"] = True

    except asyncio.TimeoutError:
        result["error"] = "timeout"
        print(f"  [{label}] 超时")
    except Exception as e:
        result["error"] = str(e)
        print(f"  [{label}] 异常: {e}")
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    return result


# =============================================================================
# 场景定义
# =============================================================================


async def two_table_deadlock(host: str = "localhost", port: int = 18600) -> dict:
    """
    两事务死锁：T1 先锁 dda_a 再锁 dda_b，T2 先锁 dda_b 再锁 dda_a。

    T1: UPDATE dda_a → UPDATE dda_b
    T2: UPDATE dda_b → UPDATE dda_a
    形成 T1 ↔ T2 死锁环。
    """
    print("=" * 60)
    print("场景: two_table_deadlock")
    print("=" * 60)

    # 1. 建表
    print("\n[Setup] 创建测试表...")
    ok = await _setup_database(host, port)
    if not ok:
        return {"scenario": "two_table_deadlock", "status": "setup_failed"}

    print("[Setup] 完成\n")

    # 2. 并发执行两个事务
    t1_first = "UPDATE dda_a SET val = 10 WHERE id = 1"
    t1_second = "UPDATE dda_b SET val = 11 WHERE id = 1"

    t2_first = "UPDATE dda_b SET val = 20 WHERE id = 1"
    t2_second = "UPDATE dda_a SET val = 21 WHERE id = 1"

    t1, t2 = await asyncio.gather(
        _transaction(host, port, 'T1', t1_first, t1_second, pause_before_second=0.8),
        _transaction(host, port, 'T2', t2_first, t2_second, pause_before_second=0.3),
    )

    # 3. 汇总
    print()
    print(f"T1: committed={t1['committed']}, killed={t1['killed']}, error={t1.get('error')}")
    print(f"T2: committed={t2['committed']}, killed={t2['killed']}, error={t2.get('error')}")

    return {
        "scenario": "two_table_deadlock",
        "t1_committed": t1["committed"],
        "t2_committed": t2["committed"],
        "t1_killed": t1["killed"],
        "t2_killed": t2["killed"],
    }
