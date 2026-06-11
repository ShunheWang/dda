# rookieDB DDA 能力补全 — 代码变更记录

> 记录了在 rookieDB 端为支持 DDA 所做的所有生产代码变更。
> 设计决策见 `docs/design.md` 第 2 节，实施 plan 见 `docs/superpowers/plans/`。

**rookieDB 路径**：`/Users/shunhewang/melbourne/cs186/berkeley-sp26-rookiedb`
**涉及文件**：8 个（6 个生产代码 + 1 个测试文件 + DummyTransaction）
**测试覆盖**：15 个 DDACapabilities 测试 + 64 个 Proj4 测试，全部通过

---

## 变更 1：事务注册表

**文件**：`Database.java`
**Commits**：`ddfdc25`, `1e219f7`, `1734680`

**背景**：rookieDB 事务是 ThreadLocal 的，DDA 作为独立连接无法访问其他连接的事务对象。需要全局注册表（transNum → TransactionImpl）来支持跨连接回滚。

### 新增字段

```java
// 第 95 行
Map<Long, TransactionImpl> transactionRegistry = new ConcurrentHashMap<>();
```

使用 `ConcurrentHashMap` 而非 `HashMap`：多个连接并发注册事务 + DDA 连接回滚事务，需要线程安全。

### 注册时机

`beginTransaction()` 完成构造后注册（第 622 行）：

```java
this.recoveryManager.startTransaction(t);
++this.numTransactions;
TransactionContext.setTransaction(t.getTransactionContext());
transactionRegistry.put(t.getTransNum(), t);  // 新增
return t;
```

### 清理时机

`TransactionImpl.cleanup()` 末尾移除（第 1107 行）：

```java
transactionContext.close();
activeTransactions.arriveAndDeregister();
transactionRegistry.remove(this.transNum);  // 新增
```

---

## 变更 2：ARIESRecoveryManager.end() 递归修复

**文件**：`ARIESRecoveryManager.java`（第 192-193 行）
**Commit**：`1c931bf`

**问题**：`end()` 先调 `cleanup()` 再设 `COMPLETE`。`TransactionImpl.cleanup()` 内部又调 `recoveryManager.end(transNum)`，形成无限递归。测试用 `DummyTransaction`（不调 `end()`），所以测试能过而生产炸。

**修复**：两行换位。

```java
// 修复前
entry.lastLSN = endLSN;
transaction.cleanup();                              // cleanup() 内部调 end() → 递归！
transaction.setStatus(Transaction.Status.COMPLETE);

// 修复后
entry.lastLSN = endLSN;
transaction.setStatus(Transaction.Status.COMPLETE); // 先设 COMPLETE
transaction.cleanup();                              // cleanup() 首行检查 COMPLETE → 直接返回
```

---

## 变更 3：TransactionContext 跨线程 unset

**文件**：`TransactionContext.java`
**Commit**：`2d89235`

**背景**：原 `unsetTransaction()` 无参版按 `Thread.currentThread().getId()` 定位线程。DDA 在执行 `\kill` 时跑在自己的连接线程上，拿到的线程 ID 是被 kill 事务的，无法清理或清理错误目标。

### 新增重载

```java
/**
 * Unset a transaction by transNum rather than by current thread.
 * Used for cross-connection rollback (DDA \kill), where the
 * rollback executes on the DDA thread, not the victim's thread.
 */
public static void unsetTransaction(long transNum) {
    threadTransactions.entrySet().removeIf(
        e -> e.getValue().getTransNum() == transNum
    );
}
```

原无参版保留不动，其他调用方不受影响。

### 调用方适配

`TransactionContext.close()` 内改用 transNum 版本（`Database.java` 第 1018 行）：

```java
// 修复前
TransactionContext.unsetTransaction();

// 修复后
TransactionContext.unsetTransaction(this.getTransNum());
```

---

## 变更 4：LockManager.removeFromAllQueues()

**文件**：`LockManager.java`
**Commit**：`205b462`

**背景**：正常 rollback 只释放已持有的锁，不从等待队列移除请求。被 DDA kill 的事务可能正卡在某个资源的等待队列中——rollback 之后 queue 里残留的 LockRequest 会导致后续事务被错误唤醒或死锁误判。

### 新增方法

```java
/**
 * Remove all lock requests for a given transaction from all resource
 * waiting queues. Used when DDA kills a transaction from another
 * connection — normal rollback releases held locks but doesn't
 * clean up pending queue entries.
 *
 * This method is synchronized to be mutually exclusive with
 * acquire/release/promote.
 */
public synchronized void removeFromAllQueues(long transNum) {
    for (ResourceEntry entry : resourceEntries.values()) {
        entry.waitingQueue.removeIf(
            req -> req.transaction.getTransNum() == transNum
        );
    }
}
```

`synchronized` 确保与 `acquire`/`release`/`promote` 互斥，线程安全。

**注意**：被移除的事务如果正阻塞在 `acquire()` 中，醒来后会发现队列里没有自己的请求，**静默返回而不持锁**。调用方（`rollbackTransaction()`）必须在 removeFromAllQueues 之后调 `rollback()` 和 `unblock()`，确保被唤醒的事务能正确处理。

---

## 变更 5：Database.rollbackTransaction()

**文件**：`Database.java`
**Commit**：`14d2b39`

**背景**：DDA 需要一个入口方法来编排跨连接回滚的三步操作。这个方法从 DDA 连接调用，操作目标事务的 LockManager 状态和 TransactionContext。

### 新增方法

```java
/**
 * Rollback a transaction from a different connection.
 * Used by DDA for cross-connection victim rollback (\kill).
 *
 * Execution order:
 * 1. Remove victim from all lock waiting queues
 * 2. Rollback via normal ARIES path (abort -> end -> release locks)
 * 3. Unblock victim thread if it was waiting in TransactionContext.block()
 */
public void rollbackTransaction(long transNum) {
    TransactionImpl t = transactionRegistry.get(transNum);
    if (t == null) {
        throw new IllegalArgumentException(
            "Transaction " + transNum + " not found in registry");
    }

    TransactionContext ctx = t.getTransactionContext();

    // 1. Clean up pending lock requests from all queues
    lockManager.removeFromAllQueues(transNum);

    // 2. Rollback via normal ARIES path
    t.rollback();

    // 3. Wake the victim thread if blocked
    ctx.unblock();
}
```

**执行顺序不能变**：必须先清理队列再 rollback——如果先 rollback，锁被释放但 queue entry 还在，后续事务可能被这个僵尸 entry 唤醒。

---

## 变更 6：\kill metacommand

**文件**：`CommandLineInterface.java`
**Commit**：`4ef0dc9`

### 新增 case

在 `parseMetaCommand()` 方法中 `\alllocks` case 之后：

```java
} else if (cmd.equals("alllocks")) {
    this.out.println(db.getAllLockInfo());
} else if (cmd.equals("kill")) {
    if (tokens.length < 2) {
        this.out.println("Usage: \\kill <transNum>");
        return;
    }
    long transNum = Long.parseLong(tokens[1]);
    db.rollbackTransaction(transNum);
    this.out.println("Transaction " + transNum + " rolled back.");
}
```

使用方式：DDA 通过 TCP 发送 `\kill <transNum>` 来执行 victim 回滚。不需要当前连接持有事务。

---

## 变更 7：事务启动时间 + getAllLockInfo

**文件**：`Database.java`, `LockManager.java`, `CommandLineInterface.java`
**Commit**：`1e219f7`, `bae32e3`

### TransactionImpl.startTime

```java
// 字段
private long startTime;

// 构造器中记录
this.startTime = System.currentTimeMillis();

// 访问器
public long getStartTime() {
    return startTime;
}
```

### Database.getTransactionTimes()

```java
public Map<Long, Long> getTransactionTimes() {
    Map<Long, Long> times = new HashMap<>();
    for (Map.Entry<Long, TransactionImpl> e : transactionRegistry.entrySet()) {
        times.put(e.getKey(), e.getValue().getStartTime());
    }
    return times;
}
```

用于阶段一的 Youngest First victim selection 规则。

### LockManager.getAllLockInfo()

```java
public synchronized String getAllLockInfo() {
    StringBuilder sb = new StringBuilder();
    sb.append("=== LockManager State ===\n");
    sb.append("transactionLocks: ").append(transactionLocks).append("\n");
    sb.append("resourceEntries:\n");
    for (Map.Entry<ResourceName, ResourceEntry> entry : resourceEntries.entrySet()) {
        sb.append("  ").append(entry.getKey()).append(" => ")
          .append(entry.getValue().toString()).append("\n");
    }
    return sb.toString();
}
```

输出全局锁状态，供 DDA 轮询。DDA 端通过 TCP 发送 `\alllocks` 获取此信息。

### Database.getAllLockInfo() 包装

```java
public String getAllLockInfo() {
    StringBuilder sb = new StringBuilder(lockManager.getAllLockInfo());
    sb.append("transactionTimes: {");
    // ... 拼接 transNum=startTime ...
    sb.append("}\n");
    return sb.toString();
}
```

职责边界：事务时间不放 LockManager 输出中（LockManager 不感知 Transaction 对象），由 Database 做聚合。

### CLI \alllocks 适配

```java
// 修复前
this.out.println(db.getLockManager().getAllLockInfo());

// 修复后
this.out.println(db.getAllLockInfo());
```

---

## 变更 8：Server 和 CLI 启用 ARIES 模式

**文件**：`Server.java`, `CommandLineInterface.java`

原代码使用纯 LockManager 模式（无 ARIES 恢复）。切换为 ARIES 模式以支持完整的 commit/rollback/recovery 流程：

```java
// Server.java / CommandLineInterface.java
// 修复前
Database db = new Database("demo", 25, new LockManager());

// 修复后
Database db = new Database("demo", 25, new LockManager(), new ClockEvictionPolicy(), true);
```

第三个参数启用 ARIES recovery manager，DDA 的 `rollbackTransaction()` 依赖 ARIES 的 abort → end → release locks 路径。

---

## 变更 9：DummyTransaction 适配

**文件**：`DummyTransaction.java`
**Commit**：`1734680`

DummyTransaction 是测试用的事务实现。ARIES 测试用它来隔离 `end()` 的递归问题。DDA 测试中需要 DummyTransaction 也支持新增的接口。

---

## 测试覆盖

**文件**：`TestDDACapabilities.java`（新增，644 行）
**Commit**：`1734680`

15 个测试覆盖了所有新增功能的正常路径和边界情况：

| # | 测试方法 | 验证内容 |
|---|---------|---------|
| 1 | `testRegistryRegisterAndCommit` | 事务 commit 后从 registry 移除 |
| 2 | `testRegistryRegisterAndRollback` | 事务 rollback 后从 registry 移除 |
| 3 | `testRegistryMultipleTransactions` | 多个事务并发注册，互不干扰 |
| 4 | `testStartTimeRecorded` | startTime 在事务创建时正确记录 |
| 5 | `testYoungestTransaction` | getTransactionTimes() 正确反映创建顺序 |
| 6 | `testGetTransactionTimesEmpty` | 无事务时返回空 Map |
| 7 | `testGetAllLockInfoIncludesTransactionTimes` | getAllLockInfo 输出含时间信息 |
| 8 | `testGetAllLockInfoEmptyTransactions` | 无事务时 getAllLockInfo 正常输出 |
| 9 | `testUnsetTransactionFromDifferentThread` | 跨线程 unset 不影响 registry |
| 10 | `testRemoveFromAllQueuesClearsPendingRequest` | kill 后等待者被正确清理 |
| 11 | `testRollbackTransactionNotFound` | 无效 transNum 抛异常 |
| 12 | `testRollbackTransactionRunning` | 正常回滚运行中的事务 |
| 13 | `testRollbackTransactionBlocked` | 回滚被锁阻塞的事务 |
| 14 | `testKillVictimOtherWaitersUnaffected` | kill 一个事务不影响其他等待者 |
| 15 | `testE2EDeadlockAndKill` | 端到端死锁 → kill → 其他事务继续 |

加上 Proj4 的 64 个 ARIES 测试，无回归。

---

## 变更文件总览

| 文件 | 改动行数 | 类型 |
|------|---------|------|
| `Database.java` | +73 | 新增 registry / rollbackTransaction / getTransactionTimes / getAllLockInfo / startTime |
| `TransactionContext.java` | +11 | 新增 unsetTransaction(long) 重载 |
| `LockManager.java` | +33 | 新增 removeFromAllQueues / getAllLockInfo |
| `CommandLineInterface.java` | +16 | 新增 \kill / \alllocks / 启用 ARIES 模式 |
| `Server.java` | +4 | 启用 ARIES 模式 |
| `ARIESRecoveryManager.java` | ±1 | end() 内两行换位 |
| `DummyTransaction.java` | +6 | 适配新增接口 |
| `TestDDACapabilities.java` | +644 | 15 个 DDA 能力测试 |

**不变的部分**：Lock/LockRequest.toString() 格式、ARIES abort/commit/restart 流程、Server 多线程模型、\alllocks 原有输出字段。
