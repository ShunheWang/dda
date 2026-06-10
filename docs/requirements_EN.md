[中文](requirements.md) | [English](requirements_EN.md)

# DDA — DB Deadlock Detection & LLM Decision System

## Project Background

rookieDB is an educational relational database with a full multi-granularity lock mechanism (S/X/IS/IX/SIX) and transaction management, but **no deadlock detection**. When multiple transactions acquire locks in reverse order, they form a cyclic wait (deadlock) and hang indefinitely.

Every major database uses hardcoded rules for deadlock victim selection:

| Database | Victim Selection Rule |
|----------|----------------------|
| MySQL (InnoDB) | Rollback the one with the smallest undo log (fewest rows inserted/updated/deleted), lowest rollback cost |
| PostgreSQL | Rollback the transaction that closed the cycle (the last lock request that caused the deadlock) |
| CockroachDB | Rollback the one with the lowest priority; if tied, kill the youngest |
| Oracle | Rollback the one that triggered the deadlock (whoever closed the cycle gets killed) |
| SQL Server | Rollback the one with the lowest `DEADLOCK_PRIORITY`; if tied, kill the cheapest to roll back |

**All rules are hardcoded. The selected transaction only knows "I was rolled back," never why.**

DDA tests a proposition: **Can LLMs replace hardcoded kernel rules to bring contextual analysis, semantic judgment, and natural language explanations to victim selection?**

---

## Core Design Decisions

> The full decision process is recorded in the design decisions document. Here are the conclusions — the engineering judgment behind each.

| Decision | Conclusion | Rationale |
|----------|-----------|-----------|
| No Worker Agent | Cut | Fixed SQL doesn't need an LLM. Concurrent transactions are pure code; DDA is the only LLM touchpoint |
| No DDA Multi-Agent Split | One DDA suffices | A 2-transaction, 1-cycle scenario doesn't justify Hand-off complexity. Split design kept as a future extension |
| No Orchestrator | Skip it | No dynamic tasks to orchestrate |
| Go straight to LLM | Included in Phase 1 | Hardcoded rules have been done for decades — no incremental value. LLM + rule fallback |

---

## Implementation Roadmap

### Phase 1: Traditional Algorithms + Comparison Baseline

Implement the full deadlock detection and recovery pipeline — wait-for graph + DFS cycle detection + **fixed rules for victim selection**.

Implement three fixed rules from major databases, swappable on the same deadlock scenario:

| Strategy | Rule | Analogous To |
|----------|------|--------------|
| Min Locks | Rollback the transaction holding the fewest locks | Similar to MySQL |
| Youngest First | Rollback the most recently started transaction | Similar to CockroachDB |
| Cycle Trigger | Rollback the transaction that closed the cycle | Similar to PostgreSQL/Oracle |

**Output**: Comparison data across all three strategies on the same scenario — which victim each picked, and why.

### Phase 2: LLM Victim Selection

Replace fixed rules with LLM decision-making. On the same deadlock scenario, compare LLM and fixed-rule decisions.

The LLM receives a description of each transaction's lock state on the deadlock cycle, analyzes, then selects a victim with a natural language explanation. If the LLM fails, Phase 1 rules serve as fallback.

**Output**: LLM decisions vs. traditional rules — choices may be the same or different, but the LLM always provides an understandable rationale.

---

## Functional Requirements

### 1. Lock State Query

Obtain the lock holding and waiting relationships of all active transactions in the database. Query failure must not affect system operation.

### 2. Deadlock Detection

Determine whether a deadlock exists (cyclic wait among transactions) from the lock state data. Detection is deterministic, independent of the LLM. Output all transactions involved in the deadlock cycle.

### 3. Victim Selection

**Phase 1**: Use fixed rules (Min Locks / Youngest First / Cycle Trigger) to select a rollback target, with swappable strategies.

**Phase 2**: The LLM analyzes candidates' held locks, wait-chain positions, and rollback cost, selects a victim, and provides a natural language explanation. If the LLM fails, rule fallback takes over.

### 4. Deadlock Resolution

Execute a rollback of the selected transaction via an independent connection.

### 5. Recovery Verification

Verify that non-victim transactions can continue execution and commit successfully; the rolled-back transaction's connection correctly receives an error.

### 6. Continuous Monitoring

The system continuously monitors lock state during concurrent transaction execution without blocking normal transaction progress.

---

## Acceptance Criteria

**Phase 1**:
- Deadlock detected within 3 polling cycles (default 500ms interval, i.e., ≤1.5s)
- ROLLBACK completed within 1 cycle after victim selection
- Three fixed rules are swappable on the same scenario, outputting different victims and rationales
- Non-victim transactions continue execution and commit successfully within 5s
- The rolled-back transaction's connection receives the correct error

**Phase 2**:
- LLM-selected victim and rationale are consistent with the actual lock state (no hallucination)
- LLM call latency ≤ 3s (for quick same-scenario comparison)
- LLM vs. fixed-rule results presented in a comparable tabular format
- If the LLM call fails, rule fallback takes over within 1 cycle

**General**:
- Single command `python dda_basic.py` to start, zero manual intervention required
- Fully observable: terminal outputs polling status, graph structure, victim selection rationale, and rollback results in real time

---

## Technical Constraints

| Constraint | Detail |
|------------|--------|
| Language | Python 3 |
| LLM Calls | Anthropic SDK (victim selection only) |
| Database | rookieDB (local single instance, multi-granularity locks) |
| Communication | TCP socket (localhost:18600) |
| Concurrency Model | asyncio | I/O-bound: TCP socket polling + LLM API calls, naturally suited to async/await; single event loop manages both concurrent transactions and DDA, avoiding multi-threaded synchronization overhead |
| Dependencies | Standard library + Anthropic SDK, no LangChain/CrewAI |

---

## Deep-Dive Roadmap

> After the basic version is complete, continue exploring along the following path. Three tiers, ordered by priority.

### Tier 1: Scenarios & Evaluation (No DB Changes Needed)

- **Multi-Deadlock Scenario Benchmark**: Three-transaction cycles, shared-lock deadlocks, multi-cycle concurrency — run all three fixed rules + LLM on each scenario, produce comparison tables
- **DDA Abstraction**: `BaseDeadlockDetector` + `BaseVictimSelector` + Strategy pattern for an extensible architecture
- **Detection Latency Analysis**: Measure deadlock-to-detection latency across scenarios to support engineering discussion

### Tier 2: rookieDB Enhancement (Modify the Database)

- **Transaction Metadata**: Add transaction start time, affected row count, and SQL text tracking to the LockManager, giving the LLM richer decision context
- **Deadlock Early Warning**: Detect growing wait chains before the cycle closes — a capability no traditional database has
- **Lock Escalation Negotiation**: Agent-to-agent negotiation over lock resources, exploring AI Agent resource management

### Tier 3: Connect to Real Databases

- **PostgreSQL Adapter**: Use `pg_locks` + `pg_stat_activity` as the data source; DDA runs independently of PostgreSQL
- **Comparative Evaluation**: PostgreSQL's built-in deadlock detection vs. DDA LLM decision-making, same scenario comparison
- **Multi-Database Support**: rookieDB / PostgreSQL / MySQL as three data sources

### Recommended Order

```
Basic Version → Multi-Scenario Benchmark → rookieDB Transaction Metadata → DDA Abstraction → PostgreSQL Adapter
```