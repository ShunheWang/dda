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
├── requirements.txt
├── docs/
│   ├── requirements.md   # 需求文档
│   ├── design.md         # 设计文档
│   └── decisions.md      # 设计决策记录
└── README.md
```

## 相关项目

- [rookieDB](https://github.com/berkeley-cs186/fa24-rookiedb) — 加州伯克利 CS186 教学数据库，本项目的数据源
- [maison-ai-learning-sandbox](https://github.com/shunhewang/maison-ai-learning-sandbox) — 学习过程记录，包含 Multi-Agent 学习笔记、Phase 1/2 demo
