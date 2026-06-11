---
name: rookiedb-safety
description: |
  对 rookieDB 的任何修改——加功能、修 bug、写测试、重构——必须先用这个 skill。
  当用户提到 rookieDB、Database.java、LockManager.java、TransactionContext.java、
  Transaction.java、ARIESRecoveryManager.java、CommandLineInterface.java、
  TestDDACapabilities.java 或任何 rookieDB 文件路径时触发。
  修改 DDA 端与 rookieDB 交互的代码时也要用。
  rookieDB 有 4 个非直观的设计约束（Phaser 生命周期、ThreadLocal 事务模型、
  锁获取静默失败、状态机隐藏分支），违反任意一个都会导致永久 hang 或数据不一致。
  这个 skill 防止这些失败。不读就动手的，出问题别来找。
---

# rookieDB 安全修改协议

## 概述

rookieDB 是一个教学数据库，有 4 个标准数据库不会有的设计约束。它们是刻意简化，不是 bug——但违反任意一个都会导致不可恢复的 hang 或静默数据损坏。标准的 Java 数据库直觉在这里不管用。

**核心原则**：每个 rookieDB 改动必须在**所有**代码路径上存活——正常路径、异常路径、被 DDA kill 的路径、测试断言失败路径。任意一个路径泄露了 Phaser party 或 ThreadLocal 关联，整个 Server 永久卡死。

**DDA 测试中踩出的 5 个 bug 有同一个根因：把 rookieDB 当标准数据库用，没有读它的实际代码行为。**

## 铁律

```
没有逐路径追踪过 4 条约束，不准说改动安全
```

## 何时使用

**必须用：**
- 给 Database、LockManager、Transaction、TransactionContext、ARIESRecoveryManager 加新方法
- 写 TestDDACapabilities 或类似的新测试
- 改动事务生命周期代码（begin、commit、rollback、close、cleanup、end）
- 加锁相关功能
- 任何触及 `activeTransactions`（Phaser）或 `threadTransactions`（ThreadLocal）的改动

**尤其要用：**
- 涉及多线程或多连接的改动
- 测试之前能过、现在卡死
- 在加异常处理或错误路径
- 你觉得不用看约束也能搞定的时候

**不要跳过：**
- "就改一行"——5 个 bug 里 3 个是一行改动引起的
- "只加个测试"——测试清理 bug 是最难调的
- "我已经很熟悉 rookieDB 了"——约束是非直观的，每次都重读

## 修改协议

### 阶段一：识别触及的约束

动手写代码前，列出你的改动触及了哪几条：

1. **Phaser 生命周期** — `Database.activeTransactions` 是 `java.util.concurrent.Phaser`。每个 `register()` 在所有代码路径上必须有且仅有一次 `arriveAndDeregister()`。少一个 party → `waitAllTransactions()` 永久等。多一个 deregister → Phaser 提前终止。
2. **ThreadLocal 事务模型** — `TransactionContext.threadTransactions` 以 `Thread.currentThread().getId()` 为 key。同一线程不能同时持有两个活跃事务。跨线程操作必须用 `unsetTransaction(long transNum)` 重载，不能用无参版。
3. **锁获取静默失败** — `LockManager.acquire()` 没有重试逻辑。被从队列移除 + unblock 后，它静默返回但不持锁。不抛异常，不返回错误码。
4. **状态机隐藏分支** — `Transaction.close()` 不总是 commit。非 RUNNING 且非 COMPLETE → 走 `cleanup()`。`ARIESRecoveryManager.end()` 必须**先**设 `COMPLETE` **再**调 `cleanup()`（反过来 = 无限递归）。

### 阶段二：追踪所有代码路径

对每个改动，逐条追踪：

- [ ] **正常路径** — 一切正常执行
- [ ] **异常路径** — 每一行可能抛异常的代码：谁做清理？谁 deregister Phaser？
- [ ] **被 DDA kill 的路径** — 如果操作中途发生 `removeFromAllQueues()` + `rollbackTransaction()` + `unblock()`，被 kill 的线程能正确处理吗？
- [ ] **测试断言失败路径** — 断言挂了，finally 块还能清理吗？

### 阶段三：按文件过规则

#### 改 `Database.java`

- 新增了 `activeTransactions.register()`？catch 块必须调 `arriveAndDeregister()` 再 rethrow。继续追问：如果 catch 本身又抛异常，谁来清理？
- 新增了 `transactionRegistry.put()`？确认 `cleanup()` 里有对应的 `remove()`。追问：如果事务没走到 cleanup() 就死了，registry entry 泄露了吗？
- 动了 `beginTransaction()`？`setTransaction()` 在调用线程已持有事务时会抛 RuntimeException——当前代码有没有 try-catch 兜底？catch 里 deregister 了吗？
- 新增了公开方法？能被 DDA 线程和事务自己的线程并发调用吗？

#### 改 `LockManager.java`

- 所有队列操作必须在 `synchronized` 块内。检查：有没有可能持着 LockManager 锁去调外部方法（死锁风险）？
- 任何 `removeFromAllQueues` 类操作：追踪被移除事务的 `acquire()` 醒来后的行为。它不会抛异常，不会重试，直接返回。调用方检查自己是否真的拿到锁了吗？

#### 改 `TransactionContext.java`

- 任何 unset/cleanup 逻辑：用的是无参版（按当前线程 ID 查）还是 `long transNum` 版（跨线程）？DDA kill 路径用错了版本 = 清理了错误的事务。
- 动了 `block()`/`unblock()`：unblock 之后，被唤醒的代码检查了自己是否已被 kill 吗？必须检查 `getStatus()`——如果是 COMPLETE，事务已死，不应该继续执行。

#### 改 `Transaction.java`

- 动了 `close()`：确认 4 种状态的行为。RUNNING → commit。COMPLETE → no-op。COMMITTING/ABORTING → cleanup。缺一个状态 = Phaser 泄露。
- 动了 `commit()` 或 `rollback()`：两个方法都要求 status == RUNNING，否则抛 IllegalStateException。DDA kill 路径必须处理这个异常。

#### 改 `ARIESRecoveryManager.java`

- `end()` 方法：顺序是**先设 COMPLETE 再调 cleanup()**。绝不能反过来。`cleanup()` 内部调用 `end()`——反过来=无限递归，单元测试测不出来（用的 DummyTransaction）。

#### 写/改测试代码

- 每个事务的清理必须放在 finally 块。不是断言后面——是 finally。断言失败会跳过 try 块剩余代码。
- 同一线程连续 `beginTransaction()`：中间插 `TransactionContext.unsetTransaction()`。
- 跨线程测试：主线程负责兜底清理。在**主线程**的 try-finally 中调 `db.rollbackTransaction(num)`。不要依赖子线程的 finally——主线程断言先挂的话，子线程可能永远走不到 finally。
- 测试类清理末尾调 `db.waitAllTransactions()` 或等价方法。这能立刻暴露 Phaser 泄露，而不是让下一个测试莫名 hang。

### 阶段四：验证

声称改动完成前，跑这些：

```bash
cd /Users/shunhewang/melbourne/cs186/berkeley-sp26-rookiedb
mvn compile -q
mvn test -Dtest="TestDDACapabilities" -DfailIfNoTests=false
mvn test -Dtest="TestRecoveryManager" -DfailIfNoTests=false
```

期望：15 个 DDACapabilities 测试全过，所有 Proj4 测试全过，BUILD SUCCESS。

如果测试卡死而不是失败：你有一个 Phaser 泄露。测试 hang = `waitAllTransactions()` 被永久阻塞。检查你的 try-finally 和 deregister 配对。

## 停止信号

- 你写了 `register()` 但没有立刻在同一个方法里写对应的 `arriveAndDeregister()`
- 你假设 `acquire()` 在事务被 kill 后会抛异常或返回 false——它不会
- 你写了只有 `if (status == RUNNING)` 分支的 `close()`
- 你在 DDA 线程执行的代码里用了无参的 `TransactionContext.unsetTransaction()`
- 你在 `ARIESRecoveryManager.end()` 里看到了 `cleanup()` 在 `setStatus(COMPLETE)` **前面**
- 测试卡死而不是失败——这永远是 Phaser party 泄露，绝不可能是"测试太慢"
- 你正准备说"这个简单，不用追踪路径"

## 五个经典 bug

调 bug 时按这个表匹配。DDA 测试遇到的所有失败都能对上：

| 模式 | 症状 | 根因 | 修复 |
|------|------|------|------|
| Phaser 僵尸 party | `waitAllTransactions()` 永久 hang | `register()` 执行了，但异常路径上没调 `arriveAndDeregister()` | register 和 deregister 之间的代码包 try-catch |
| close() 静默跳过 | Phaser 泄露，异常后测试 hang | `Transaction.close()` 只处理 RUNNING，其他状态 no-op | 加 `else if (status != COMPLETE) cleanup()` |
| ThreadLocal 冲突 | `setTransaction()` 抛 RuntimeException | 同一线程两次 `beginTransaction()` 中间没 `unsetTransaction()` | 两次调用之间插 `TransactionContext.unsetTransaction()` |
| 断言跳过清理 | 测试 hang，一个失败导致后面所有测试全挂 | try 块里断言失败，同一个 try 块里的清理被跳过 | 清理移到 finally 块 |
| unset 用错重载 | 事务清理了错误的线程，或者根本没清理 | 无参 `unsetTransaction()` 用当前线程 ID 查——DDA kill 路径跑在错误的线程上 | 跨线程清理用 `unsetTransaction(long transNum)` |