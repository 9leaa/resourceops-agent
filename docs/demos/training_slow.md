# Demo: Training Slowdown

This demo shows the bounded LLM planner path for a broad "training is slow" question.

## Command

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --planner-mode llm --report-mode llm
```

Legacy combined mode is also supported:

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --agent-mode llm_planner
```

## Expected Flow

1. `ToolCatalog` describes available local diagnostic tools.
2. LLM proposes a candidate `ToolPlan`.
3. `PlanValidator` checks the plan for known tools, valid args, budget, duplicates, resource type, and permission level.
4. Invalid plans fall back to deterministic planning.
5. Tools execute through `ToolRegistry`.
6. LLM report mode rewrites the final report from existing evidence and compact context.

## What To Look For

Trace should include planning details:

```bash
python main.py trace <run_id> --json | jq '.steps[] | select(.step_type == "llm_planner")'
```

Workspace should include the final selected plan:

```bash
jq . var/runs/<run_id>/plan.json
```

The report should explain whether the likely bottleneck is GPU memory, CPU/DataLoader, memory pressure, swap pressure, or missing GPU telemetry.

## Safety Boundary

The LLM cannot call tools directly. It proposes a plan only. Tool execution remains inside `ResourceAgent` and `ToolRegistry`.
