# 五个经典 Bug 速查

DDA 测试中踩出的所有失败都能对上这个表。调 bug 时按模式匹配。

| 模式 | 症状 | 根因 | 修复 |
|------|------|------|------|
| Phaser 僵尸 party | `waitAllTransactions()` 永久 hang | `register()` 执行了，但异常路径上没调 `arriveAndDeregister()` | register 和 deregister 之间的代码包 try-catch |
| close() 静默跳过 | Phaser 泄露，异常后测试 hang | `Transaction.close()` 只处理 RUNNING，其他状态 no-op | 加 `else if (status != COMPLETE) cleanup()` |
| ThreadLocal 冲突 | `setTransaction()` 抛 RuntimeException | 同一线程两次 `beginTransaction()` 中间没 `unsetTransaction()` | 两次调用之间插 `TransactionContext.unsetTransaction()` |
| 断言跳过清理 | 测试 hang，一个失败导致后面所有测试全挂 | try 块里断言失败，同一个 try 块里的清理被跳过 | 清理移到 finally 块 |
| unset 用错重载 | 事务清理了错误的线程，或者根本没清理 | 无参 `unsetTransaction()` 用当前线程 ID 查——DDA kill 路径跑在错误的线程上 | 跨线程清理用 `unsetTransaction(long transNum)` |