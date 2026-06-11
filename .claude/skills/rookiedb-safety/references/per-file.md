# 按文件修改规则

动哪个文件就看哪一节。

---

## Database.java

- 新增了 `activeTransactions.register()`？catch 块必须调 `arriveAndDeregister()` 再 rethrow。继续追问：如果 catch 本身又抛异常，谁来清理？
- 新增了 `transactionRegistry.put()`？确认 `cleanup()` 里有对应的 `remove()`。追问：如果事务没走到 cleanup() 就死了，registry entry 泄露了吗？
- 动了 `beginTransaction()`？`setTransaction()` 在调用线程已持有事务时会抛 RuntimeException——当前代码有没有 try-catch 兜底？catch 里 deregister 了吗？
- 新增了公开方法？能被 DDA 线程和事务自己的线程并发调用吗？

## LockManager.java

- 所有队列操作必须在 `synchronized` 块内。检查：有没有可能持着 LockManager 锁去调外部方法（死锁风险）？
- 任何 `removeFromAllQueues` 类操作：追踪被移除事务的 `acquire()` 醒来后的行为。它不会抛异常，不会重试，直接返回。调用方检查自己是否真的拿到锁了吗？

## TransactionContext.java

- 任何 unset/cleanup 逻辑：用的是无参版（按当前线程 ID 查）还是 `long transNum` 版（跨线程）？DDA kill 路径用错了版本 = 清理了错误的事务。
- 动了 `block()`/`unblock()`：unblock 之后，被唤醒的代码检查了自己是否已被 kill 吗？必须检查 `getStatus()`——如果是 COMPLETE，事务已死，不应该继续执行。

## Transaction.java

- 动了 `close()`：确认 4 种状态的行为。RUNNING → commit。COMPLETE → no-op。COMMITTING/ABORTING → cleanup。缺一个状态 = Phaser 泄露。
- 动了 `commit()` 或 `rollback()`：两个方法都要求 status == RUNNING，否则抛 IllegalStateException。DDA kill 路径必须处理这个异常。

## ARIESRecoveryManager.java

- `end()` 方法：顺序是**先设 COMPLETE 再调 cleanup()**。绝不能反过来。`cleanup()` 内部调用 `end()`——反过来 = 无限递归，单元测试测不出来（用的 DummyTransaction）。

## 测试代码

- 每个事务的清理必须放在 finally 块。不是断言后面——是 finally。断言失败会跳过 try 块剩余代码。
- 同一线程连续 `beginTransaction()`：中间插 `TransactionContext.unsetTransaction()`。
- 跨线程测试：主线程负责兜底清理。在**主线程**的 try-finally 中调 `db.rollbackTransaction(num)`。不要依赖子线程的 finally——主线程断言先挂的话，子线程可能永远走不到 finally。
- 测试类清理末尾调 `db.waitAllTransactions()` 或等价方法。这能立刻暴露 Phaser 泄露，而不是让下一个测试莫名 hang。