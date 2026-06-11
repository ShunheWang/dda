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

rookieDB 是教学数据库，有 4 个标准数据库没有的设计约束。DDA 测试中踩出的 5 个 bug 有同一个根因：**把 rookieDB 当标准数据库用，没有读它的实际代码行为**。任意一个约束违反 → 永久 hang 或静默数据损坏。

## 铁律

```
没有逐路径追踪过 4 条约束，不准说改动安全
```

## 何时使用

**必须用：**
- 给 Database、LockManager、Transaction、TransactionContext、ARIESRecoveryManager 加新方法
- 写 TestDDACapabilities 或类似的新测试
- 改动事务生命周期（begin、commit、rollback、close、cleanup、end）
- 任何触及 `activeTransactions`（Phaser）或 `threadTransactions`（ThreadLocal）的改动

**尤其要用：**
- 改动涉及多线程或多连接
- 测试之前能过、现在卡死
- 你在加异常处理或错误路径
- 你觉得不用看约束也能搞定的时候——这时候最危险

**不要跳过：**
- "就改一行"——5 个 bug 里 3 个是一行改动引起的
- "只加个测试"——测试清理 bug 是最难调的
- "我已经很熟悉 rookieDB 了"——约束非直观，每次都重读

## 修改协议

### 阶段一：识别触及的约束

列出你的改动触及了哪几条（详见 `references/constraints.md`）：

1. **Phaser 生命周期** — 每个 `register()` 在所有路径上必须有且仅有一次 `arriveAndDeregister()`
2. **ThreadLocal 事务模型** — 同线程不能有两个活跃事务；跨线程用 `unsetTransaction(long)` 重载
3. **锁获取静默失败** — `acquire()` 被 kill 后不抛异常，静默返回不持锁
4. **状态机隐藏分支** — `close()` 不总是 commit；`ARIES.end()` 必须先 `setStatus(COMPLETE)` 再 `cleanup()`

### 阶段二：追踪所有代码路径

对每个改动，逐条确认：

- [ ] **正常路径** — 正常执行
- [ ] **异常路径** — 每行可能抛异常的代码：谁清理？谁 deregister Phaser？
- [ ] **被 DDA kill 的路径** — `removeFromAllQueues()` + `rollbackTransaction()` + `unblock()` 中途发生，被 kill 线程能正确处理吗？
- [ ] **测试断言失败路径** — 断言挂了，finally 还能清理吗？

### 阶段三：按文件过规则

读 `references/per-file.md`，按你改动的文件逐条对照。

### 阶段四：验证

改动完成后，跑这些确认无回归：

```bash
cd /Users/shunhewang/melbourne/cs186/berkeley-sp26-rookiedb
mvn compile -q
mvn test -Dtest="TestDDACapabilities" -DfailIfNoTests=false
mvn test -Dtest="TestRecoveryManager" -DfailIfNoTests=false
```

期望：15 个 DDACapabilities 全过，所有 Proj4 全过，BUILD SUCCESS。

**测试卡死而不是失败 = Phaser 泄露。** 检查 try-finally 和 deregister 配对。

## 停止信号

- 写了 `register()` 但没在同一个方法里写对应的 `arriveAndDeregister()`
- 假设 `acquire()` 被 kill 后会抛异常或返回 false——它不会
- 写了只有 `if (status == RUNNING)` 分支的 `close()`
- 在 DDA 线程执行的代码里用了无参 `TransactionContext.unsetTransaction()`
- 在 `ARIESRecoveryManager.end()` 里看到了 `cleanup()` 在 `setStatus(COMPLETE)` **前面**
- 测试卡死而不是失败——永远是 Phaser 泄露，不可能是"测试太慢"
- 准备说"这个简单，不用追踪路径"

## 调 bug 时

读 `references/bugs.md`，按模式匹配。DDA 测试遇到的所有失败都在那 5 个模式里。