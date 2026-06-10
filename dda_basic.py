"""
DDA (DB Deadlock Agent) — 死锁检测与 LLM Victim Selection
==========================================================

在 rookieDB 外部监控锁状态，检测死锁，用 LLM 选定 victim，解除死锁。

阶段一（传统规则）: 实现三种主流数据库的固定规则作为对比基线
阶段二（LLM 决策）: 用 LLM 分析上下文选定 victim + 规则 fallback

运行方式:
  1. 先启动 rookieDB Server
  2. python dda_basic.py
"""

import os
import pathlib


def main():
    print("=" * 60)
    print("DDA — DB Deadlock Agent")
    print("=" * 60)
    print()
    print("状态：待实现")
    print()
    print("实施计划见 docs/design.md")
    print("需求文档见 docs/requirements.md")
    print("决策记录见 docs/decisions.md")


if __name__ == "__main__":
    main()
