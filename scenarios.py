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

from dda.connection import execute_sql, is_error, open_connection


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
    writer: Optional[asyncio.StreamWriter] = None

    try:
        reader, writer = await open_connection(host, port)

        # BEGIN
        resp = await execute_sql(reader, writer, 'BEGIN')
        print(f"  [{label}] BEGIN → {resp}")

        # 第一步 UPDATE（获取第一个锁）
        resp = await execute_sql(reader, writer, first_update)
        print(f"  [{label}] {first_update} → {resp}")

        # 暂停，让另一个事务也获取它的第一个锁
        await asyncio.sleep(pause_before_second)

        # 第二步 UPDATE（可能阻塞，等待另一个事务释放锁）
        print(f"  [{label}] {second_update} → 等待锁...")
        resp = await execute_sql(reader, writer, second_update, timeout=30.0)
        print(f"  [{label}] {second_update} → {resp}")

        # 成功获取锁 → COMMIT
        if is_error(resp):
            result["killed"] = True
            result["error"] = resp
        else:
            resp = await execute_sql(reader, writer, 'COMMIT')
            print(f"  [{label}] COMMIT → {resp}")
            if is_error(resp):
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


async def _setup_database(host: str, port: int) -> bool:
    """创建测试表并插入初始数据。"""
    try:
        reader, writer = await open_connection(host, port)

        for sql in [
            'CREATE TABLE dda_a (id INT, val INT)',
            'CREATE TABLE dda_b (id INT, val INT)',
        ]:
            resp = await execute_sql(reader, writer, sql)
            if 'error' in resp.lower() or 'already exists' in resp.lower():
                print(f"  [Setup] {sql} → {resp}")

        for sql in [
            "INSERT INTO dda_a VALUES (1, 100)",
            "INSERT INTO dda_b VALUES (1, 200)",
        ]:
            resp = await execute_sql(reader, writer, sql)
            if 'error' in resp.lower():
                print(f"  [Setup] {sql} → {resp}")

        writer.close()
        await writer.wait_closed()
        return True
    except Exception as e:
        print(f"  [Setup] 失败: {e}")
        return False


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

    print("\n[Setup] 创建测试表...")
    ok = await _setup_database(host, port)
    if not ok:
        return {"scenario": "two_table_deadlock", "status": "setup_failed"}

    print("[Setup] 完成\n")

    t1_first = "UPDATE dda_a SET val = 10 WHERE id = 1"
    t1_second = "UPDATE dda_b SET val = 11 WHERE id = 1"

    t2_first = "UPDATE dda_b SET val = 20 WHERE id = 1"
    t2_second = "UPDATE dda_a SET val = 21 WHERE id = 1"

    t1, t2 = await asyncio.gather(
        _transaction(host, port, 'T1', t1_first, t1_second, pause_before_second=0.8),
        _transaction(host, port, 'T2', t2_first, t2_second, pause_before_second=0.3),
    )

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
