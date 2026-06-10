# DDA 项目说明（给 Claude Code）

## 项目概述

DDA（DB Deadlock Agent）是一个死锁检测与 LLM 决策系统。rookieDB 有完整的多粒度锁但没有死锁检测，DDA 在外部监控、检测死锁、用 LLM 选定 victim、执行回滚。

核心命题：用 LLM 替代数据库内核固定规则做 victim selection。

## 技术栈

- **语言**：Python 3
- **LLM 调用**：Anthropic SDK（仅用于 victim selection，不是 Agent 循环）
- **数据库**：rookieDB（Java），TCP socket (localhost:18600)
- **并发**：asyncio（并发事务 + DDA 轮询共享事件循环）
- **依赖**：标准库 + `anthropic`，无 LangChain/CrewAI

## 架构原则

### 代码 vs LLM 边界

| 环节 | 谁做 | 理由 |
|------|------|------|
| SQL 执行 | 纯代码 | 确定性操作，不需要 LLM |
| 锁状态解析 | 纯代码 | 文本解析，确定性的 |
| Wait-for Graph | 纯代码 | 图算法，确定性的 |
| DFS 找环 | 纯代码 | 图算法，确定性的 |
| Victim Selection | **LLM** | 需要语义判断、上下文分析 |
| ROLLBACK 执行 | 纯代码 | 确定性操作 |

**原则**：LLM 只在 victim selection 这一个环节出场。其他地方不用——不是所有地方都需要 AI。

### 设计文档

需求文档 (`docs/requirements.md`)、设计文档 (`docs/design.md`)、决策记录 (`docs/decisions.md`) 是项目的真相源。写代码前先看相关文档。

## 运行

```bash
# rookieDB Server 必须先启动
# 启动方式看 rookieDB 项目的 README

pip install -r requirements.txt
python dda_basic.py
```

## 环境变量

```bash
ANTHROPIC_API_KEY=sk-...     # API key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic  # DeepSeek 兼容层
```

可以从 `.env` 文件加载，也可以直接在终端 export。
