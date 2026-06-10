# DDA — DB Deadlock Agent

**用 LLM 替代数据库内核固定规则，做 deadlock victim selection。**

rookieDB 是教学级关系数据库，实现了完整的多粒度锁机制但没有死锁检测。DDA 在外部轮询 LockManager 状态，构建 wait-for graph，检测死锁环，然后用 LLM 分析上下文选定 victim 并给出自然语言解释——而不是像 MySQL/PostgreSQL 那样用固定规则静默杀掉一个事务。

## 核心命题

> 能否把传统数据库内核中的死锁检测逻辑外包给 AI？

所有主流数据库的 victim selection 都是固定规则：

| 数据库 | 规则 |
|--------|------|
| MySQL | 回滚 undo log 最小的 |
| PostgreSQL | 回滚触发环闭合的那个 |
| CockroachDB | 回滚优先级最低的 |
| Oracle | 回滚导致死锁的那个 |
| SQL Server | 回滚 DEADLOCK_PRIORITY 最低的 |

**没有一个数据库能用自然语言解释"我为什么杀你"。DDA 可以。**

## 架构

```
并发事务 (纯代码 asyncio)
     │
     ├── T1: BEGIN → UPDATE Students → UPDATE Courses → 阻塞
     ├── T2: BEGIN → UPDATE Courses → UPDATE Students → 阻塞
     │
     └── DDA (asyncio Task, 独立 socket 连接)
           ├── 轮询 LockManager (\alllocks)
           ├── 构建 Wait-for Graph
           ├── DFS 找环
           ├── Victim Selection
           │   ├── 阶段一: 固定规则 (Min Locks / Youngest / Cycle Trigger)
           │   └── 阶段二: LLM 决策 (+ fallback)
           └── 独立连接 ROLLBACK
```

DDA 采用 **Sidecar 架构**：不侵入数据库内核，作为独立进程在外部轮询锁状态。这意味着不绑定特定数据库——深挖路线图中 PostgreSQL 适配只需将数据源从 `\alllocks` 换成 `pg_locks` + `pg_stat_activity`，其余逻辑（图构建、找环、victim selection）完全复用。

## 两种实施阶段

### 阶段一：传统算法基线

实现三种主流数据库的固定规则，同一场景下对比输出——建立对比基线。

### 阶段二：LLM 替代

同一场景用 LLM 选定 victim，与阶段一对比。LLM 给出的是"有理由的决策"，不是一个冷冰冰的事务 ID。

## 快速开始

```bash
# 1. 启动 rookieDB
cd /path/to/rookiedb
java -cp target/classes edu.berkeley.cs186.database.cli.Server &

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY

# 4. 运行
python dda_basic.py
```

## 文件结构

```
dda/
├── dda_basic.py          # 主程序
├── pyproject.toml        # 项目配置（依赖、linting）
├── requirements.txt
├── LICENSE
├── docs/
│   ├── requirements.md   # 背景、功能需求、实施路线、验收标准
│   ├── design.md         # 架构、数据流、组件接口、LLM prompt 设计
│   └── decisions.md      # 关键设计决策的博弈过程与结论
└── README.md
```

## 相关项目

- [rookieDB](https://github.com/ShunheWang/berkeley-sp26-rookiedb) — 本项目的数据源。基于伯克利 CS186 教学数据库骨架代码，新增了 `LockManager.getAllLockInfo()`、`\alllocks` metacommand 等辅助方法，支撑 DDA 的锁状态采集
- [maison-ai-learning-sandbox](https://github.com/shunhewang/maison-ai-learning-sandbox) — 本项目的学习前身，包含 Multi-Agent 学习笔记、Orchestrator-Worker demo、死锁复现实验

## 参考文章

以下是本项目依赖的核心知识来源：

| 文章 | 学到了什么 |
|------|-----------|
| [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) (Anthropic) | Agent 设计哲学：Workflows vs Agents，什么时候不该用 LLM，Prompt Chaining / Routing / Parallelization 等 6 种编排模式 |
| [Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system) (Anthropic) | Orchestrator-Worker 实战、Subagent 单一职责原则、性能对比数据（90.2% 提升）、生产挑战与经验 |
| [Claude Agent Patterns](https://github.com/anthropics/claude-cookbooks/issues/303) | 7 种 Agent 模式速查：Subagent Orchestration、Prompt Chaining、Parallelization、Evaluator-Optimizer、Routing、Master-Clone、Programmatic Orchestration |
| [ai-agents-for-beginners](https://github.com/microsoft/ai-agents-for-beginners) (Microsoft) | Lesson 3-8：Agentic Design Patterns、Tool Use、Multi-Agent 协作模式（Hand-off、Collaborative Filtering 等 6 大构建模块） |
| [mcp-for-beginners](https://github.com/microsoft/mcp-for-beginners) (Microsoft) | MCP 协议核心概念：Server/Client 架构、Tool/Resource/Prompt 三种原语、JSON-RPC 2.0 消息层 |
| [Claude Agent SDK Best Practices](https://skywork.ai/blog/claude-agent-sdk-best-practices-ai-agents-2025/) | 生产实践经验：最小权限（每个 Agent 只给必要的 tool）、上下文隔离、错误隔离 |
