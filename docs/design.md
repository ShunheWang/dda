[中文](design.md) | [English](design_EN.md)

# DDA 设计文档

> 状态：部分完成（rookieDB 能力补全已完成，DDA 端设计待讨论）
> 最后更新：2026-06-11

---

## 1. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    DDA (Python + asyncio)                │
│                                                         │
│  ┌─────────────┐  ┌──────────┐  ┌───────────────┐      │
│  │ 并发事务执行  │  │ DDA 监控  │  │  LLM Victim   │      │
│  │  (纯代码)    │  │  Task    │  │   Selector    │      │
│  │             │  │           │  │               │      │
│  │ T1: socket1 │  │ 轮询锁状态 │  │ Anthropic SDK │      │
│  │ T2: socket2 │  │ 构造WFG   │  │               │      │
│  │             │  │ DFS找环   │  │               │      │
│  │ asyncio     │  │ 选victim  │  │               │      │
│  │ gather()    │  │           │  │               │      │
│  └──────┬──────┘  └─────┬────┘  └───────┬───────┘      │
│         │               │               │               │
└─────────┼───────────────┼───────────────┼───────────────┘
          │ TCP socket    │               │
          ▼               ▼               │
┌─────────────────────────────────────────────────────────┐
│               rookieDB Server (Java)                     │
│                                                         │
│  \alllocks  → 锁状态查询（已有）                          │
│  \kill      → 跨连接回滚（新增）                          │
│                                                         │
│  ARIESRecoveryManager + LockManager + TransactionImpl   │
└─────────────────────────────────────────────────────────┘
```

**关键边界**：
- LLM 只在 victim selection 一个环节调用
- 其他所有环节（SQL 执行、锁状态解析、WFG 构造、DFS 找环、ROLLBACK 执行）纯代码
- DDA 通过独立 TCP socket 连接运行，不嵌入 rookieDB 内核

---

## 2. rookieDB 能力补全

> DDA 需要、但 rookieDB 当前不提供的能力。讨论于 2026-06-11。

### 2.1 跨连接事务回滚（`\kill`）

**问题**：rookieDB 的事务是 ThreadLocal 的。每个连接的 Transaction 对象只存在于自己线程的上下文中（`TransactionContext.threadTransactions` 以 `Thread.getId()` 为 key）。DDA 作为独立连接，无法访问或回滚其他连接的事务。

**设计思路**：Database 增加事务注册表（transNum → TransactionImpl 映射），提供一个公开的回滚方法。回滚执行在 DDA 线程，但操作的目标是全局共享的 LockManager（线程安全）。

#### 2.1.1 事务注册表

**位置**：`Database.java`

新增 `Map<Long, TransactionImpl> transactionRegistry`，使用 `ConcurrentHashMap`（多线程并发：一个连接注册事务、DDA 连接回滚事务）。

- `beginTransaction()` 完成构造后注册
- `cleanup()` 末尾移除

#### 2.1.2 rollbackTransaction(long transNum)

**位置**：`Database.java`（新增公开方法）

DDA 调用的入口。执行顺序：

1. **清理等待队列**：调用 `LockManager.removeFromAllQueues(transNum)`，移除被 kill 事务在所有资源等待队列中的 `LockRequest`
2. **回滚**：调用 `t.rollback()`，走正规 ARIES 路径（abort → end → 释放锁）
3. **唤醒线程**：调用 `ctx.unblock()`，如果目标事务的线程正卡在 `TransactionContext.block()` 里，将其唤醒

#### 2.1.3 ARIES.end() 顺序修复

**位置**：`ARIESRecoveryManager.java` 第 192-193 行

**问题**：当前代码先调 `transaction.cleanup()` 再调 `setStatus(COMPLETE)`。`TransactionImpl.cleanup()` 内部调用 `recoveryManager.end(transNum)`，形成递归。ARIES 单元测试用 `DummyTransaction`（其 `cleanup()` 不调 `end()`），因此测试能过而生产炸。

**修法**：两行换位——先 `setStatus(COMPLETE)` 再 `cleanup()`。`TransactionImpl.cleanup()` 首行检查 `COMPLETE` 后直接返回，递归终结。

#### 2.1.4 等待队列清理

**位置**：`LockManager.java`（新增方法）

被 kill 的事务可能正在 LockManager 的资源等待队列中排队（请求锁被阻塞）。正常 rollback 只释放已持有的锁，不会从等待队列移除。需要新增 `removeFromAllQueues(long transNum)` 遍历所有 `ResourceEntry.waitingQueue`，移除该事务。

该方法是 `synchronized` 的，与 `acquire`/`release`/`promote` 互斥，保证线程安全。

#### 2.1.5 跨线程 ThreadLocal 清理

**位置**：`TransactionContext.java`

**问题**：`unsetTransaction()` 通过 `Thread.currentThread().getId()` 定位线程。回滚在 DDA 线程执行时，拿到的线程 ID 是 DDA 的，不是被 kill 事务的，导致无法清理或清理错误目标。

**修法**：新增 `unsetTransaction(long transNum)` 重载，按 transNum 在 `threadTransactions` 中查找并移除。原无参版本保留不动，其他调用方不受影响。

`TransactionContext.close()` 内改用 transNum 版本调用。

#### 2.1.6 `\kill` metacommand

**位置**：`CommandLineInterface.java`

新增 metacommand：`\kill <transNum>`。解析并调用 `db.rollbackTransaction(transNum)`。该命令从 DDA 连接执行，不需要当前连接持有事务。

#### 2.1.7 已知边界：竞态窗口

`\kill` 存在一个理论竞态窗口：DDA 从 `transactionRegistry` 拿到目标事务后、调用 `rollback()` 前，目标事务可能恰好自然完成（锁被释放、正常 commit）。此时 `t.rollback()` 抛出 `IllegalStateException`（"transaction not in running state"），`ctx.unblock()` 走不到。

**实际不会触发**：DDA 只在 `\alllocks` 确认死锁后才发 `\kill`。victim 事务一定处于 `block()` 等待状态（`Status.RUNNING`），不可能在 kill 的短短几毫秒内"刚好自己完成"。如果死锁已经自行解除，kill 失败反而是正确行为——不需要回滚一个没有死锁的事务。

### 2.2 事务启动时间

**问题**：阶段一的 Youngest First 规则需要知道"谁是最年轻的事务"。当前 `TransactionImpl` 不记录创建时间。虽可通过 `transNum` 间接推导（按创建顺序递增），但这是隐式依赖实现细节，不够明确。

**设计**：

- `TransactionImpl` 构造时记录 `System.currentTimeMillis()`，新增 `getStartTime()` 访问器
- `Database` 新增 `getTransactionTimes()`，返回 `Map<Long, Long>`（transNum → startTime）
- CLI 的 `\alllocks` 调用 `db.getAllLockInfo()`（Database 包装方法），输出中附加事务时间信息

**职责边界**：事务时间不放在 `LockManager` 输出中——LockManager 不感知 Transaction 对象。由 `Database` 做聚合，CLI 做格式拼接。

### 2.3 改动范围汇总

| 文件 | 改动 | 类型 |
|------|------|------|
| `Database.java` | 事务注册表 + `rollbackTransaction()` + `getTransactionTimes()` + `getAllLockInfo()` 包装 + TransactionImpl 时间字段 | 新增 |
| `ARIESRecoveryManager.java` | `end()` 内两行换位 | 修复 |
| `LockManager.java` | `removeFromAllQueues()` | 新增 |
| `TransactionContext.java` | `unsetTransaction(long)` 重载 | 新增 |
| `CommandLineInterface.java` | `\kill` case + `\alllocks` 调新方法 | 新增/修改 |

**不变的部分**：`Lock`/`LockRequest.toString()` 格式、ARIES abort/commit/restart 流程、Server 多线程模型、`\alllocks` 原有输出字段。

---

## 3. DDA 端设计

> 待讨论

### 3.1 待设计章节

- DDA 组件职责与接口（PollingMonitor、LockParser、WFGBuilder、CycleDetector、VictimSelector、RollbackExecutor）
- 数据流（轮询 → 解析 → 图构建 → 找环 → Victim Selection → 回滚）
- LockSnapshot 数据结构
- WaitForGraph 数据结构
- VictimSelector 接口（固定规则 + LLM 两种实现）
- LLM Prompt 设计
- 错误处理策略
- 可观测性设计

---

## 4. 实施顺序

```
rookieDB 能力补全（本文第 2 节）
  → DDA 端设计（第 3 节）
    → 阶段一：传统算法 + 对比基线
      → 阶段二：LLM Victim Selection
```