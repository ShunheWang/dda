#!/usr/bin/env python3
"""
DDA (DB Deadlock Agent) — 死锁检测与 Victim Selection
========================================================

在 rookieDB 外部监控锁状态，检测死锁，选定 victim，解除死锁。

阶段一：三种固定规则（Min Locks / Youngest First / Cycle Trigger）对比
阶段二：LLM Victim Selection（后续实现）

运行方式:
  1. 先启动 rookieDB Server
  2. python dda_basic.py
  3. python dda_basic.py -v  # 详细模式
"""

import argparse
import asyncio

from dda.selector import (
    CycleTriggerSelector,
    MinLocksSelector,
    YoungestFirstSelector,
    VictimSelector,
)
from dda.monitor import PollingMonitor


# =============================================================================
# Runner — 单次策略运行
# =============================================================================


async def _run_once(
    host: str,
    port: int,
    selector: VictimSelector,
    interval: float,
    scenario_fn,
) -> dict:
    """用指定策略跑一轮场景，返回运行结果。"""
    stop_event = asyncio.Event()
    monitor = PollingMonitor(
        host=host, port=port, interval=interval, selector=selector
    )

    monitor_task = asyncio.create_task(monitor.run(stop_event))
    await asyncio.sleep(0.3)  # 让 monitor 开始第一轮轮询

    try:
        scenario_result = await scenario_fn(host, port)
    except Exception as e:
        scenario_result = {"error": str(e)}

    await asyncio.sleep(2.0)  # 等死锁解除后的收尾轮询
    stop_event.set()
    await monitor_task

    return {
        "strategy": selector.name,
        "cycles": monitor.cycle_num,
        "deadlocks_detected": monitor.deadlocks_detected,
        "transactions_killed": monitor.transactions_killed,
        "scenario": scenario_result,
    }


# =============================================================================
# main()
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(
        description="DDA — DB Deadlock Agent"
    )
    parser.add_argument(
        "--host", default="localhost", help="rookieDB host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=18600, help="rookieDB port (default: 18600)"
    )
    parser.add_argument(
        "--interval", type=float, default=0.5,
        help="轮询间隔秒数 (default: 0.5)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="详细模式：显示每轮轮询输出"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("DDA — DB Deadlock Agent")
    print("=" * 60)
    print(f"目标: {args.host}:{args.port}")
    print(f"轮询间隔: {args.interval}s")
    print()

    # === 检查 rookieDB 是否可达 ===
    print("检查 rookieDB 连接...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=3.0
        )
        writer.close()
        await writer.wait_closed()
        print("  ✓ rookieDB 在线\n")
    except Exception:
        print("  ✗ 无法连接 rookieDB。请先启动:")
        print("    java -cp target/classes edu.berkeley.cs186.database.cli.Server &\n")
        return

    # === 加载场景 ===
    from scenarios import two_table_deadlock

    scenario = two_table_deadlock
    scenario_name = getattr(scenario, "__name__", "unknown")
    print(f"场景: {scenario_name}\n")

    # === 三种策略逐一跑 ===
    selectors: list[VictimSelector] = [
        MinLocksSelector(),
        YoungestFirstSelector(),
        CycleTriggerSelector(),
    ]

    results = []
    for i, selector in enumerate(selectors):
        if i > 0:
            print("\n" + "-" * 60 + "\n")
        print(f"策略 [{i + 1}/{len(selectors)}]: {selector.name}")
        print("-" * 40)

        result = await _run_once(
            args.host, args.port, selector, args.interval, scenario
        )
        results.append(result)
        print(f"  结果: {result['deadlocks_detected']} deadlock(s), "
              f"{result['transactions_killed']} killed")

    # === 对比表格 ===
    print("\n")
    print("=" * 70)
    print("对比汇总")
    print("=" * 70)
    header = f"{'策略':<18} {'轮询':<6} {'死锁':<6} {'回滚':<6}"
    print(header)
    print("-" * 70)
    for r in results:
        print(
            f"{r['strategy']:<18} "
            f"{r['cycles']:<6} "
            f"{r['deadlocks_detected']:<6} "
            f"{r['transactions_killed']:<6}"
        )
    print()


if __name__ == "__main__":
    asyncio.run(main())
