---
name: rookiedb-safety
description: |
  Mandatory checklist and constraints for ANY modification to rookieDB source code or tests.
  Use this skill whenever the user mentions rookieDB, Database.java, LockManager.java, TransactionContext.java,
  Transaction.java, ARIESRecoveryManager.java, CommandLineInterface.java, TestDDACapabilities.java, or any
  other rookieDB file. Also use it when modifying DDA code that interacts with rookieDB via TCP.
  This skill prevents the most common and catastrophic rookieDB bugs: Phaser zombie parties, ThreadLocal
  violations, silent lock acquisition failures, and transaction cleanup leaks.
---

# rookieDB 安全修改指南

> **刚性 checklist**——每次改 rookieDB 代码，动手前必须逐条读过。不加例外、不凭直觉。

## 为什么有这个 skill

rookieDB 有 4 个非常规设计约束，是标准 Java 数据库教科书不会教你的。在 DDA 测试中踩出了 5 个 bug，根因全是"假设 rookieDB 行为跟标准数据库一样"。这些约束不是 bug——是 rookieDB 作为教学数据库的刻意简化——但违反任意一个都会导致不可恢复的 hang 或数据不一致。

---

## 核心约束（改代码前必须记住的 4 条）

### 约束 1：Phaser 是事务生命周期的唯一闸门

`Database.activeTransactions` 是一个 `java.util.concurrent.Phaser`。每当事务开始时 `register()`，结束时 `arriveAndDeregister()`。`waitAllTransactions()` 通过 `isTerminated()` 判断所有事务是否结束。

**核心规则**：每个 `register()` 必须在**所有代码路径**上有且仅有一次 `arriveAndDeregister()`。

违反后果：`waitAllTransactions()` 永久阻塞，整个 Server hang，无超时、无异常。

**容易出错的地方**：
- `register()` 之后的任何代码抛异常 → party 泄露
- 事务通过非标准路径结束（如被 DDA kill、异常中断）→ `cleanup()` 可能不调
- 同一个事务在多个地方调了 `arriveAndDeregister()` → Phaser 提前终止

### 约束 2：TransactionContext 是 ThreadLocal 的

`TransactionContext.threadTransactions` 是 `static Map<Long, TransactionContext>`，key 是 `Thread.currentThread().getId()`。**同一个线程同一时间只能持有一个活跃事务**。`setTransaction()` 发现同线程已有事务→直接抛 `RuntimeException`。

**核心规则**：
- 连续 `beginTransaction()` 之间必须 `unsetTransaction()`
- 跨线程回滚（DDA \kill）不能用 `unsetTransaction()`（它按当前线程 ID 查）——必须用 `unsetTransaction(long transNum)` 重载
- 在子线程创建的事务，主线程无法通过 `TransactionContext` 访问

### 约束 3：LockManager.acquire() 被中断后静默返回

`LockManager.acquire()` 没有"被 kill 后抛异常"的逻辑。当 DDA 调用 `removeFromAllQueues()` + `unblock()` 后，被阻塞的 `acquire()` 从 `wait()` 中醒来，发现队列里没有自己的请求，**静默返回而不持锁**。调用方不会收到任何异常或状态码表示"你没拿到锁"。

**核心规则**：
- 被 kill 的事务从 `block()` 返回后，必须检查自己是否真的拿到了锁
- 不能假设"unblock 后一定能继续正常执行"

### 约束 4：Transaction 状态机有隐藏分支

```
RUNNING → COMMITTING → COMPLETE   (commit 路径)
RUNNING → ABORTING  → COMPLETE   (rollback 路径)
```

**核心规则**：
- `close()` 在 `RUNNING` → `commit()`，非 `RUNNING` 且非 `COMPLETE` → `cleanup()`。**不能假设 close() 总是走 commit 路径**
- `ARIESRecoveryManager.end()` 必须**先设 `COMPLETE` 再调 `cleanup()`**（反过来会递归，因为 `cleanup()` 内部调 `end()`）
- `Transaction.rollback()` 和 `commit()` 要求 `status == RUNNING`，否则抛 `IllegalStateException`

---

## 修改前 checklist（按文件）

### 如果改 `Database.java`

- [ ] 新增了 `activeTransactions.register()`？确认所有异常路径都有 `arriveAndDeregister()`
- [ ] 新增了 `transactionRegistry.put()`？确认 `cleanup()` 中有对应的 `remove()`
- [ ] 调整了 `beginTransaction()`？确认 `setTransaction()` 在 `register()` 之后、但有 try-catch 兜底
- [ ] 新增了公开方法？确认不会暴露 `TransactionImpl` 的内部状态（并发安全）

### 如果改 `LockManager.java`

- [ ] 新增了 `synchronized` 方法？确认不会在持有锁时调用外部方法（死锁风险）
- [ ] 操作了 `waitingQueue`？确认在 `synchronized` 块内
- [ ] 新增了队列清理逻辑？确认被移除事务的 `acquire()` 能正确处理"静默返回"情况

### 如果改 `TransactionContext.java`

- [ ] 新增了 `unset` 相关逻辑？确认区分了"同线程"和"跨线程"两种场景
- [ ] 修改了 `block()`/`unblock()`？确认被 kill 的事务从 `block()` 返回后能识别自己已死亡

### 如果改 `Transaction.java`

- [ ] 修改了 `close()`？确认覆盖了 RUNNING / COMMITTING / ABORTING / COMPLETE 四种状态
- [ ] 修改了状态转换逻辑？确认 ARIES.end() 的 `setStatus(COMPLETE)` → `cleanup()` 顺序

### 如果改 `ARIESRecoveryManager.java`

- [ ] 动了 `end()` 方法？确认 `setStatus(COMPLETE)` 在 `cleanup()` **之前**
- [ ] 动了 abort/commit 逻辑？确认最终都会经过 `end()`（或等价清理）

### 如果改测试代码

- [ ] 每个事务的 cleanup 在 finally 块中（包括异常路径和断言失败路径）
- [ ] 同线程连续 `beginTransaction()` 之间有 `unsetTransaction()`
- [ ] 跨线程测试：主线程负责兜底清理（finally 中 `rollbackTransaction` 或 `unsetTransaction`）
- [ ] 测试结束时调了 `db.waitAllTransactions()` 或等价等待
- [ ] 新增死锁场景测试？确认 kill 后其他事务能正常继续

---

## 修改后验证

1. **编译**：`mvn compile -q`
2. **相关测试**：至少跑 DDACapabilities（15 个）和 Proj4（64 个）
3. **手动追踪**：通读改动的每一条异常路径——假设每个可能抛异常的地方都抛了，逐条确认清理逻辑
4. **并发场景**：如果涉及多线程/锁，手动走一遍时序，画图确认每个线程的每个事务最终都走到了 cleanup

---

## 参考：5 个典型 bug

| Bug | 违反的约束 | 表现 | 触发条件 |
|-----|-----------|------|---------|
| beginTransaction 无 try-catch | 约束 1（Phaser） | `waitAllTransactions()` 永久 hang | setTransaction() 抛异常 |
| close() 非 RUNNING 不调 cleanup | 约束 1 + 4（Phaser + 状态机） | Phaser party 泄露 | 事务被 kill 或异常中断后 close() |
| 同线程连续 beginTransaction() | 约束 2（ThreadLocal） | RuntimeException | 第二次 setTransaction() 时线程已有事务 |
| 测试 try-finally 缺失 | 约束 1（Phaser） | 断言失败跳过清理 → hang | 测试断言失败 |
| unsetTransaction 用错版本 | 约束 2（ThreadLocal） | 清理了错误的事务 | DDA 线程调无参版本 |

---

## 禁止事项

- **不要**假设 `LockManager.acquire()` 被中断后会抛异常——它不会
- **不要**假设 `Transaction.close()` 总是走 commit 路径——非 RUNNING 状态走 else-if
- **不要**在多线程测试中让子线程负责事务清理——主线程出问题时子线程的 finally 可能不执行
- **不要**在没有 `unsetTransaction()` 的情况下同线程连续 beginTransaction
- **不要**在 `ARIESRecoveryManager.end()` 中先调 `cleanup()` 再设 `COMPLETE`