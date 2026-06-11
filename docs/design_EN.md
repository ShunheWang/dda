[中文](design.md) | [English](design_EN.md)

# DDA Design Document

> Status: Partially complete (rookieDB capability additions complete; DDA-side design pending)
> Last updated: 2026-06-11

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    DDA (Python + asyncio)                │
│                                                         │
│  ┌─────────────┐  ┌──────────┐  ┌───────────────┐      │
│  │ Concurrent   │  │ DDA      │  │  LLM Victim   │      │
│  │ Transactions │  │ Monitor  │  │   Selector    │      │
│  │  (pure code) │  │  Task    │  │               │      │
│  │             │  │           │  │ Anthropic SDK │      │
│  │ T1: socket1 │  │ Poll lock │  │               │      │
│  │ T2: socket2 │  │ state     │  │               │      │
│  │             │  │ Build WFG │  │               │      │
│  │ asyncio     │  │ DFS cycle │  │               │      │
│  │ gather()    │  │ detection │  │               │      │
│  └──────┬──────┘  └─────┬────┘  └───────┬───────┘      │
│         │               │               │               │
└─────────┼───────────────┼───────────────┼───────────────┘
          │ TCP socket    │               │
          ▼               ▼               │
┌─────────────────────────────────────────────────────────┐
│               rookieDB Server (Java)                     │
│                                                         │
│  \alllocks  → Lock state query (existing)               │
│  \kill      → Cross-connection rollback (new)            │
│                                                         │
│  ARIESRecoveryManager + LockManager + TransactionImpl   │
└─────────────────────────────────────────────────────────┘
```

**Key boundaries:**
- LLM is only invoked for victim selection
- All other steps (SQL execution, lock state parsing, WFG construction, DFS cycle detection, ROLLBACK execution) are pure code
- DDA runs via an independent TCP socket connection, not embedded in the rookieDB kernel

---

## 2. rookieDB Capability Additions

> Capabilities DDA needs that rookieDB currently lacks. Discussed on 2026-06-11.

### 2.1 Cross-Connection Transaction Rollback (`\kill`)

**Problem:** rookieDB transactions are ThreadLocal. Each connection's Transaction object exists only within its own thread context (`TransactionContext.threadTransactions` keyed by `Thread.getId()`). DDA, as an independent connection, cannot access or rollback other connections' transactions.

**Design approach:** Database gains a transaction registry (transNum → TransactionImpl mapping) and exposes a public rollback method. Rollback executes on DDA's thread but operates on the globally shared LockManager (which is thread-safe).

#### 2.1.1 Transaction Registry

**Location:** `Database.java`

Add `Map<Long, TransactionImpl> transactionRegistry` using `ConcurrentHashMap` (multi-threaded concurrent access: one connection registers transactions, DDA connection rolls them back).

- `beginTransaction()` registers after construction
- `cleanup()` removes at the end

#### 2.1.2 rollbackTransaction(long transNum)

**Location:** `Database.java` (new public method)

DDA's entry point. Execution order:

1. **Clean wait queues:** Call `LockManager.removeFromAllQueues(transNum)` to remove the victim's pending lock requests from all resource queues
2. **Rollback:** Call `t.rollback()`, following the normal ARIES path (abort → end → release locks)
3. **Unblock thread:** Call `ctx.unblock()` to wake the victim thread if it's stuck in `TransactionContext.block()`

#### 2.1.3 ARIES.end() Order Fix

**Location:** `ARIESRecoveryManager.java` lines 192-193

**Problem:** The current code calls `transaction.cleanup()` before `setStatus(COMPLETE)`. `TransactionImpl.cleanup()` internally calls `recoveryManager.end(transNum)`, causing recursion. ARIES unit tests use `DummyTransaction` (whose `cleanup()` does NOT call `end()`), so tests pass but production breaks.

**Fix:** Swap the two lines — set `COMPLETE` first, then call `cleanup()`. `TransactionImpl.cleanup()`'s first line checks for `COMPLETE` and returns immediately, breaking the recursion.

#### 2.1.4 Wait Queue Cleanup

**Location:** `LockManager.java` (new method)

A killed transaction may have pending `LockRequest` entries in resource waiting queues (blocked requesting locks). Normal rollback only releases held locks, not queued requests. Add `removeFromAllQueues(long transNum)` to iterate all `ResourceEntry.waitingQueue` entries and remove those belonging to the victim.

This method is `synchronized`, mutually exclusive with `acquire`/`release`/`promote`, ensuring thread safety.

#### 2.1.5 Cross-Thread ThreadLocal Cleanup

**Location:** `TransactionContext.java`

**Problem:** `unsetTransaction()` locates the thread via `Thread.currentThread().getId()`. When rollback executes on DDA's thread, the thread ID belongs to DDA, not the victim, causing failure or wrong cleanup target.

**Fix:** Add `unsetTransaction(long transNum)` overload that searches by transNum in `threadTransactions`. The original no-arg version is preserved for other callers. `TransactionContext.close()` switches to the transNum version.

#### 2.1.6 `\kill` Metacommand

**Location:** `CommandLineInterface.java`

New metacommand: `\kill <transNum>`. Parses and calls `db.rollbackTransaction(transNum)`. Executed from DDA's connection — no transaction required on the calling connection.

#### 2.1.7 Known Edge Case: Race Window

`\kill` has a theoretical race window: after DDA retrieves the target transaction from `transactionRegistry` but before `rollback()` executes, the target could coincidentally finish naturally (lock released, normal commit). In this case, `t.rollback()` throws `IllegalStateException` ("transaction not in running state"), and `ctx.unblock()` is never reached.

**Not triggerable in practice:** DDA only issues `\kill` after confirming a deadlock via `\alllocks`. The victim transaction is guaranteed to be in `block()` waiting state (`Status.RUNNING`), and cannot "just happen to finish" within the few milliseconds of kill execution. If the deadlock has already resolved itself, the kill failing is the correct behavior — there is no need to rollback a transaction that isn't deadlocked.

### 2.2 Transaction Start Time

**Problem:** Phase 1's Youngest First rule needs to identify "which transaction is youngest." `TransactionImpl` currently records no creation time. While `transNum` is assigned in monotonically increasing order (implying later creation for larger numbers), this is an implicit dependency on implementation details and not sufficiently explicit.

**Design:**

- `TransactionImpl` records `System.currentTimeMillis()` at construction; new `getStartTime()` accessor
- `Database` adds `getTransactionTimes()`, returning `Map<Long, Long>` (transNum → startTime)
- CLI's `\alllocks` calls `db.getAllLockInfo()` (Database wrapper method), which appends transaction time info to the output

**Responsibility boundary:** Transaction times are not placed in `LockManager` output — LockManager has no awareness of Transaction objects. `Database` handles aggregation; CLI handles formatting.

### 2.3 Change Summary

| File | Change | Type |
|------|--------|------|
| `Database.java` | Transaction registry + `rollbackTransaction()` + `getTransactionTimes()` + `getAllLockInfo()` wrapper + TransactionImpl time fields | New |
| `ARIESRecoveryManager.java` | Swap two lines in `end()` | Fix |
| `LockManager.java` | `removeFromAllQueues()` | New |
| `TransactionContext.java` | `unsetTransaction(long)` overload | New |
| `CommandLineInterface.java` | `\kill` case + `\alllocks` call update | New/Modify |

**Unchanged:** `Lock`/`LockRequest.toString()` format, ARIES abort/commit/restart flow, Server multi-threaded model, existing `\alllocks` output fields.

---

## 3. DDA-Side Design

> Pending discussion

### 3.1 Chapters to Design

- DDA component responsibilities and interfaces (PollingMonitor, LockParser, WFGBuilder, CycleDetector, VictimSelector, RollbackExecutor)
- Data flow (polling → parsing → graph construction → cycle detection → victim selection → rollback)
- LockSnapshot data structure
- WaitForGraph data structure
- VictimSelector interface (fixed rule + LLM implementations)
- LLM Prompt design
- Error handling strategy
- Observability design

---

## 4. Implementation Order

```
rookieDB capability additions (Section 2)
  → DDA-side design (Section 3)
    → Phase 1: Traditional algorithms + baseline comparison
      → Phase 2: LLM Victim Selection
```
