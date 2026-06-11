# 4 大核心约束详解

rookieDB 有 4 个标准数据库不会有的设计约束。它们是刻意简化，不是 bug——但违反任意一个都会导致不可恢复的 hang 或静默数据损坏。标准的 Java 数据库直觉在这里不管用。

---

## 约束 1：Phaser 生命周期

`Database.activeTransactions` 是 `java.util.concurrent.Phaser`。事务开始时 `register()`，结束时 `arriveAndDeregister()`。`waitAllTransactions()` 通过 `isTerminated()` 判断所有事务是否结束。

**核心规则**：每个 `register()` 在所有代码路径上必须有且仅有一次 `arriveAndDeregister()`。

**违反后果**：`waitAllTransactions()` 永久阻塞。整个 Server hang，无超时、无异常。

**容易出错的场景**：
- `register()` 之后的任何代码抛异常 → party 泄露
- 事务通过非标准路径结束（被 DDA kill、异常中断）→ `cleanup()` 可能不调
- 同一个事务在多个地方调了 `arriveAndDeregister()` → Phaser 提前终止

## 约束 2：ThreadLocal 事务模型

`TransactionContext.threadTransactions` 是 `static Map<Long, TransactionContext>`，key 是 `Thread.currentThread().getId()`。**同一个线程同一时间只能持有一个活跃事务**。`setTransaction()` 发现同线程已有事务 → 直接抛 `RuntimeException`。

**核心规则**：
- 连续 `beginTransaction()` 之间必须 `unsetTransaction()`
- 跨线程回滚（DDA `\kill`）不能用无参 `unsetTransaction()`（它按当前线程 ID 查）——必须用 `unsetTransaction(long transNum)` 重载
- 在子线程创建的事务，主线程无法通过 `TransactionContext` 访问

## 约束 3：LockManager.acquire() 静默失败

`LockManager.acquire()` 没有"被 kill 后抛异常"的逻辑。当 DDA 调用 `removeFromAllQueues()` + `unblock()` 后，被阻塞的 `acquire()` 从 `wait()` 中醒来，发现队列里没有自己的请求，**静默返回而不持锁**。调用方不会收到任何异常或状态码表示"你没拿到锁"。

**核心规则**：
- 被 kill 的事务从 `block()` 返回后，必须检查自己是否真的拿到了锁
- 不能假设"unblock 后一定能继续正常执行"

## 约束 4：Transaction 状态机隐藏分支

```
RUNNING → COMMITTING → COMPLETE    (commit 路径)
RUNNING → ABORTING  → COMPLETE    (rollback 路径)
```

**核心规则**：
- `close()` 在 RUNNING → `commit()`；非 RUNNING 且非 COMPLETE → `cleanup()`。不能假设 close() 总是走 commit 路径
- `ARIESRecoveryManager.end()` 必须**先设 COMPLETE 再调 cleanup()**。反过来会递归——`cleanup()` 内部调 `end()`，而 `end()` 又调 `cleanup()`
- `Transaction.rollback()` 和 `commit()` 要求 status == RUNNING，否则抛 `IllegalStateException`