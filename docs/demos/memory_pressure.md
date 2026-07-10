# Demo: Memory Pressure / OOM

This demo shows memory and swap diagnosis, pending approvals, dry-run action execution, and workspace updates.

## Optional: Create Controlled Memory Pressure

Use a bounded stress process in one terminal:

```bash
python scripts/stress_memory.py --mb 2048 --max-mb 4096 --duration 300 --chunk-mb 256
```

Use values that are safe for your machine.

## Diagnosis Command

In another terminal:

```bash
python main.py diagnose "为什么内存快满了？" \
  --resource-type memory \
  --interactive-approval
```

With LLM planning/reporting:

```bash
python main.py diagnose "为什么内存快满了？" \
  --resource-type memory \
  --planner-mode llm \
  --report-mode llm \
  --interactive-approval
```

## Expected Flow

1. Memory snapshot, top memory processes, OOM lookup, and related context are collected.
2. Detectors identify memory pressure, swap pressure, OOM events, or process-level memory hogging when present.
3. Risky recommendations create approvals.
4. Interactive approval lists all pending approvals for the run.
5. `y` approves dry-run execution.
6. `r` attempts real execution, but it is blocked unless real-action gates are enabled.
7. Todos, trace, and workspace are updated after approval/rejection/action execution.

## Inspect Results

```bash
python main.py workspace <run_id>
jq . var/runs/<run_id>/trace/approvals.json
jq . var/runs/<run_id>/trace/action_results.json
jq '.[] | select(.source == "action_executor")' var/runs/<run_id>/todos.json
```

## Real Execution Warning

Real `kill_process` should only be tested against a controlled process you started yourself.

```bash
RESOURCEOPS_ENABLE_REAL_ACTIONS=true \
RESOURCEOPS_REAL_ACTION_ALLOWLIST=kill_process \
python main.py execute-real <approval_id> --confirm-real
```

For routine validation, prefer dry-run approval.
