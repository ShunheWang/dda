# DDA (DB Deadlock Agent) — 死锁检测与 Victim Selection
#
# 模块索引：
#   models      — 数据结构（HeldLock, LockSnapshot, WaitForGraph, Cycle）
#   connection  — TCP 通信辅助（连接、读取、命令执行）
#   parser      — \alllocks 输出解析 → LockSnapshot
#   wfg         — Wait-for Graph 构造
#   detector    — DFS 找环
#   selector    — Victim 选择策略（MinLocks / YoungestFirst / CycleTrigger / LLM）
#   executor    — \kill 执行
#   monitor     — PollingMonitor 主循环
