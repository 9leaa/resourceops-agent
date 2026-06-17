# ResourceOps Agent

ResourceOps Agent is a local-first resource diagnosis agent for GPU, CPU, and Memory problems. It is based on the IncidentOps harness shape, but the product scope is real local resource diagnosis rather than simulated service incidents.

Current status: **V1-P0**.

## What Works Now

- New ResourceOps project skeleton.
- Resource-focused schemas:
  - `ResourceIncident`
  - `DiagnosisRun`
  - `DiagnosisStep`
  - `EvidenceItem`
  - `DiagnosisFinding`
  - `Recommendation`
  - `Approval`
- CLI command renamed to `diagnose`.
- FastAPI endpoint renamed to `/diagnose`.
- ToolRegistry skeleton with permission levels, validation, timeout, preview, and summary fields.
- Approval store/service with simulated dangerous-action execution.
- SQLite TraceStore for runs, steps, tool calls, evidence items, findings, and approvals.
- Per-run workspace directories under `var/runs/<run_id>/`.

P0 intentionally does not execute real GPU/CPU/Memory tools yet. That starts in V1-P1.

## Quick Start

```bash
cd /home/zcj/resourceops-agent
python main.py diagnose "为什么 CPU 很高？"
```

The command creates a diagnosis run, writes a trace to `var/resourceops.sqlite3`, and prints a P0 report.

Show recent runs:

```bash
python main.py runs
```

Show a trace:

```bash
python main.py trace <run_id>
```

Run the API:

```bash
uvicorn app.api:app --host 0.0.0.0 --port 18000
```

HTTP diagnose:

```bash
curl -sS -X POST http://localhost:18000/diagnose \
  -H 'content-type: application/json' \
  -d '{"description":"为什么 GPU 显存满了？","resource_type":"gpu"}'
```

## Project Layout

```text
resourceops-agent/
├── app/       # CLI, FastAPI, schemas
├── agent/     # ResourceAgent and planning
├── tools/     # ToolRegistry and future resource tools
├── approval/  # Human approval store/service
├── trace/     # SQLite trace store
├── eval/      # Future fixture and live smoke eval
├── scripts/   # Future stress scripts
├── tests/     # P0 smoke tests
└── var/       # Runtime state, ignored by git in a future repo
```

## V1 Roadmap

- V1-P1: real GPU/CPU/Memory/Process tools.
- V1-P2: deterministic ResourceAgent plans.
- V1-P3: detectors.
- V1-P4: report and approval wiring for dangerous recommendations.
- V1-P5: fixture eval, live smoke eval, and stress scripts.
- V1-P6: complete FastAPI demo flow.
