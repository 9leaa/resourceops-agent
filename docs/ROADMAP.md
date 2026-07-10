# Roadmap

ResourceOps Agent is currently a local MVP for AI infrastructure diagnosis. The next major direction is V2: making the system more resilient, reusable, and capable of longer-running observations without weakening the safety boundary.

## V2-P1: Hooks and Error Recovery

Add structured pre/post hooks around planning, tool execution, reporting, approval, and action execution.

Expected value:

- centralize audit and safety checks
- record recoverable failures consistently
- make retry/fallback behavior explicit
- keep feature code from scattering lifecycle logic

## V2-P2: Diagnostic Skills

Introduce reusable diagnosis playbooks such as GPU OOM, CPU bottleneck, memory leak, swap pressure, and suspicious process review.

Expected value:

- keep prompt and detector logic scenario-specific
- make planner behavior easier to inspect
- prepare the project for future skill search without adding RAG yet

## V2-P3: Machine Memory and Baselines

Persist safe machine-level baselines such as normal GPU memory, CPU load, swap usage, known long-running jobs, and user preferences.

Expected value:

- distinguish normal local workload from anomalies
- improve report context without relying only on one snapshot
- support safer recommendations

## V2-P4: Subagents

Split specialized work into GPU, CPU, Memory, Process, and Report agents under one lead coordinator.

Expected value:

- clearer ownership of domain logic
- easier testing of each diagnosis path
- better foundation for multi-step investigations

## V2-P5: Background Sampling

Support bounded observation windows such as 30-60 seconds of GPU utilization, CPU load, memory growth, and process RSS changes.

Expected value:

- detect trends instead of only snapshots
- diagnose intermittent slowdowns
- keep runtime bounded and user-controlled

## V2-P6: Guarded Resource Monitor

Allow a background monitor to detect anomalies and create diagnosis runs automatically. Risky actions still require explicit approval.

Expected value:

- make ResourceOps useful before the user notices the issue
- preserve the approval-gated safety model
- generate replayable traces for every monitor-triggered diagnosis

## Out of Scope for the Current MVP

- Kubernetes / Prometheus / DCGM integration
- Web dashboard
- MCP integration
- autonomous dangerous remediation
- broad real-action catalog
- multi-tenant remote execution
