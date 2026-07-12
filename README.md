# ResourceOps Agent

ResourceOps Agent is a safe local AI infrastructure diagnosis agent for GPU, CPU, memory, OOM, swap, and process bottlenecks.

It combines deterministic resource detectors with bounded LLM planning/reporting, approval-gated dry-run actions, trace replay, workspace isolation, and eval fixtures.

Traditional tools show metrics. ResourceOps explains likely causes, supporting evidence, and safe next actions.

![CI](https://github.com/9leaa/resourceops-agent/actions/workflows/ci.yml/badge.svg)

## Why This Project Matters

AI/ML developers often debug training slowdowns, GPU memory pressure, CPU saturation, memory leaks, swap pressure, and suspicious Python/Jupyter processes using scattered tools like `nvidia-smi`, `top`, `ps`, and logs.

ResourceOps turns those raw signals into an evidence-backed diagnosis workflow:

- collect real local resource signals
- build a validated tool plan
- extract structured evidence and findings
- generate a diagnosis report
- gate risky actions behind approval and dry-run
- persist trace/workspace artifacts for replay and debugging

## Current Status

MVP complete: safe local resource diagnosis, bounded LLM planning/reporting, approval-gated dry-run actions, trace replay, workspace isolation, CLI, FastAPI, and eval fixtures.

## Core Capabilities

### 1. Evidence-backed resource diagnosis

- Collects real CPU, memory, GPU, OOM, and process signals.
- Detects GPU memory pressure, CPU saturation, memory pressure, swap pressure, OOM events, and process-level bottlenecks.
- Generates structured evidence, findings, recommendations, approvals, action results, and final reports.

### 2. Bounded LLM planning and reporting

- LLM can propose a tool plan, but cannot execute tools directly.
- `PlanValidator` checks tool names, arguments, budgets, duplicates, resource type, and permission levels.
- Invalid LLM plans fall back to deterministic plans.
- LLM report mode rewrites only the final report from existing evidence, findings, recommendations, approvals, and compact report context.

### 3. Safe action workflow

- Dangerous recommendations create approval records.
- Approval runs `ActionExecutor` dry-run by default.
- Real execution is gated behind environment flags, allowlists, approval, dry-run, pre-check, and explicit confirmation.
- Every run stores trace, todos, approvals, action results, and workspace artifacts.

## Quick Start

```bash
git clone https://github.com/9leaa/resourceops-agent.git
cd resourceops-agent

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python main.py diagnose "为什么 CPU 很高？"
```

ResourceOps works on machines without NVIDIA GPUs. GPU tools return structured `no_gpu` / `nvidia-smi unavailable` results instead of crashing.

### LLM Report Mode

```bash
python main.py diagnose "为什么 CPU 很高？" --report-mode llm
```

The legacy combined mode is also supported:

```bash
python main.py diagnose "为什么 CPU 很高？" --agent-mode llm_report
```

LLM settings are read from `.env` or environment variables:

```bash
RESOURCEOPS_LLM_BASE_URL=http://127.0.0.1:3000/v1
RESOURCEOPS_LLM_API_KEY=replace-with-your-key
RESOURCEOPS_LLM_MODEL=replace-with-your-model
RESOURCEOPS_LLM_SERVICE_TIER=fast
RESOURCEOPS_LLM_PLANNER_MAX_TOKENS=512
RESOURCEOPS_LLM_REPORT_MAX_TOKENS=640
RESOURCEOPS_LLM_MAX_RETRIES=1
RESOURCEOPS_LLM_RETRY_BACKOFF_SECONDS=1.0
RESOURCEOPS_STORE_LLM_PAYLOADS=false
```

`RESOURCEOPS_LLM_SERVICE_TIER` is optional and provider-specific. The planner and report output limits keep long LLM calls within common upstream gateway limits. Transient `429/502/503/504` and transport failures are retried once by default. Full prompts and responses are not stored unless `RESOURCEOPS_STORE_LLM_PAYLOADS=true`; hashes, lengths, latency, and redacted previews are always available in the compact LLM summary.

### LLM Planner Mode

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --planner-mode llm --report-mode llm
```

The legacy combined mode is also supported:

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --agent-mode llm_planner
```

### Interactive Approval Mode

```bash
python main.py diagnose "为什么内存快满了？" \
  --resource-type memory \
  --interactive-approval
```

Interactive approval supports:

```text
y=approve dry-run / r=approve and real-execute / n=reject / s=skip / q=quit
```

Real execution still requires explicit environment enablement and allowlisting. Without those gates, `r` is blocked safely.

## Demo Scenarios

### Demo 1: GPU Memory Pressure

```bash
python main.py diagnose "为什么 GPU 显存满了？" --resource-type gpu
```

Expected diagnosis:

- GPU memory pressure detected when available
- GPU process list inspected
- suspicious process identified by PID, owner, command preview, and memory usage
- risky action creates approval
- approval produces dry-run `ActionResult` instead of killing the process

Full walkthrough: [docs/demos/gpu_memory_pressure.md](docs/demos/gpu_memory_pressure.md)

### Demo 2: Training Slowdown

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --planner-mode llm --report-mode llm
```

Expected diagnosis:

- LLM proposes a tool plan
- `PlanValidator` validates or falls back to deterministic planning
- GPU / CPU / memory tools run through `ToolRegistry`
- report explains whether the bottleneck is GPU memory, CPU/DataLoader, memory, or swap pressure

Full walkthrough: [docs/demos/training_slow.md](docs/demos/training_slow.md)

### Demo 3: Memory Pressure / OOM

```bash
python main.py diagnose "为什么内存快满了？" --resource-type memory --interactive-approval
```

Expected diagnosis:

- memory and swap pressure summarized
- top memory processes ranked
- OOM lookup included when available
- dangerous recommendations require approval
- approved action writes dry-run `ActionResult` to trace/workspace

Full walkthrough: [docs/demos/memory_pressure.md](docs/demos/memory_pressure.md)

## Safety Model

ResourceOps separates diagnosis, approval, dry-run, and real execution.

- Diagnostic tools are executed through `ToolRegistry`.
- LLM planning is bounded by `ToolCatalog`, `ToolPlan`, and `PlanValidator`.
- LLM reporting receives compact evidence context and cannot create new tool outputs.
- `approve` and interactive `y` run dry-run action execution only.
- Real execution uses the separate `execute-real` entrypoint.
- Real execution requires `RESOURCEOPS_ENABLE_REAL_ACTIONS=true`, `RESOURCEOPS_REAL_ACTION_ALLOWLIST=<action>`, approval, successful dry-run, pre-check, post-check, and `--confirm-real`.

Example real execution command:

```bash
RESOURCEOPS_ENABLE_REAL_ACTIONS=true \
RESOURCEOPS_REAL_ACTION_ALLOWLIST=renice_process \
python main.py execute-real <approval_id> --confirm-real
```

`kill_process` is intentionally dangerous and should only be allowlisted for controlled test processes.

## Trace, Workspace, and Debug Bundle

Each diagnosis run is persisted to SQLite trace storage and to an isolated workspace under `var/runs/<run_id>/`.

```text
var/runs/<run_id>/
  metadata.json
  plan.json
  report.md
  remediation_summary.md
  summary/
    run_summary.json
    run_summary.md
  raw/
    tool_outputs.jsonl
    llm_calls.jsonl        # only when explicitly enabled
  compact/
    report_context.json
    llm_calls_summary.json
  trace/
    steps.json             # lightweight artifact index
    evidence.json
    findings.json
    approvals.json
    action_results.json
```

Useful commands:

```bash
python main.py runs
python main.py trace <run_id>
python main.py trace <run_id> --full
python main.py trace <run_id> --step llm_planner
python main.py trace <run_id> --llm
python main.py trace <run_id> --summary-json
python main.py trace <run_id> --json

python main.py workspace <run_id>
python main.py workspace <run_id> --show-report
python main.py workspace <run_id> --show-context

python main.py bundle <run_id>
python main.py bundle <run_id> --include-llm-payloads
tar -tzf var/bundles/<run_id>.tar.gz
```

The default trace view is a deterministic run summary. `--json` intentionally keeps the legacy full SQLite trace output for script compatibility. The report is a diagnosis-stage snapshot; approval and action changes are recorded separately in `remediation_summary.md`.

## Eval and Tests

Run unit and integration tests:

```bash
python -m pytest -q
```

Run deterministic fixture eval:

```bash
python eval/run_eval.py
```

Run live smoke eval against the current machine:

```bash
python eval/run_live_smoke.py
```

Run bounded stress scripts manually:

```bash
python scripts/stress_cpu.py --duration 10 --workers 2
python scripts/stress_memory.py --mb 256 --duration 10
python scripts/stress_gpu_memory.py --mb 512 --duration 10 --yes
```

## FastAPI

```bash
uvicorn app.api:app --host 0.0.0.0 --port 18000 --workers 1
```

The current FastAPI service uses in-process report jobs and an in-memory job
registry. Run it with a single API worker. `RESOURCEOPS_REPORT_WORKERS` only
controls report threads inside that one process; it is not a Uvicorn worker
count. Multi-process API serving needs an external job queue or a SQLite-backed
job lease before it is safe.

HTTP diagnosis:

```bash
curl -sS -X POST http://localhost:18000/diagnose \
  -H 'content-type: application/json' \
  -d '{"description":"为什么 GPU 显存满了？","resource_type":"gpu"}'
```

Docker Compose is also available:

```bash
docker compose up --build
```

## Project Layout

```text
resourceops-agent/
  actions/    # approval-gated dry-run and real action executor
  agent/      # ResourceAgent, planning, validation, reports, todos
  app/        # CLI, FastAPI, schemas
  approval/   # approval store/service and trace synchronization
  docs/       # design notes, demos, roadmap, development history
  eval/       # deterministic fixture eval and live smoke eval
  scripts/    # bounded local stress scripts
  tests/      # unit and integration tests
  tools/      # ToolRegistry and real local resource tools
  trace/      # SQLite trace store and replay models
  workspace/  # per-run workspace writer and debug bundle export
  var/        # local runtime state, ignored by git
```

## Known Limitations

- ResourceOps is local-machine focused. It does not yet integrate with Kubernetes, Prometheus, DCGM, or cloud observability APIs.
- LLM behavior depends on the configured OpenAI-compatible endpoint; deterministic fallback remains the safety baseline.
- Live smoke output depends on the current machine, GPU availability, process list, and system permissions.
- Real execution is intentionally narrow and gated. It is not an autonomous remediation system.
- The current CLI is the primary user experience; no web dashboard is included.

## Roadmap

The next major direction is V2: hooks/error recovery, reusable diagnostic skills, machine memory/baselines, subagents, background sampling, and eventually a guarded autonomous monitor.

See [docs/ROADMAP.md](docs/ROADMAP.md) for details.

## Resume Summary

ResourceOps Agent is an AI Infra / Agent Engineering project that demonstrates:

- local observability tooling for GPU/CPU/memory/process diagnosis
- bounded LLM tool planning with validation and deterministic fallback
- evidence-backed reporting from structured traces
- human approval and dry-run action execution before risky operations
- workspace isolation and debug bundles for reproducibility
- CLI, FastAPI, eval fixtures, and integration tests

## Development History

Detailed implementation stages and design notes are kept out of the README first screen:

- [docs/DEVELOPMENT_HISTORY.md](docs/DEVELOPMENT_HISTORY.md)
- [docs/DESIGN.md](docs/DESIGN.md)
- [docs/ResourceOps_Agent_DESIGN.md](docs/ResourceOps_Agent_DESIGN.md)
- [docs/STAGE_PAUSE_SUMMARY.md](docs/STAGE_PAUSE_SUMMARY.md)
