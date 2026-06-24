# Stage Pause Summary

Current stage: **V1-P4**

## Implemented

- Resource-focused schemas for incidents, runs, steps, tool calls, evidence, findings, recommendations, and approvals.
- Real local tools for CPU, memory, GPU, OOM lookup, and process inspection.
- Deterministic GPU / CPU / Memory / Mixed plans.
- ResourceAgent executes planned tools through ToolRegistry.
- Detectors convert tool results into `EvidenceItem` and `DiagnosisFinding` records.
- Reports include resource checks, key evidence, findings, recommendations, and tool errors.
- Dangerous recommendations create Approval records.
- Runs with pending approvals enter `waiting_approval` status.
- TraceStore persists runs, steps, tool calls, evidence items, findings, and approvals.
- CLI and FastAPI diagnosis flow.

## Verified

```bash
python -m compileall -q app agent approval trace tools scripts eval tests
conda run -n zcj_hello python -m pytest -q
```

Latest local result: `31 passed`.

## Current Boundary

V1-P4 creates approval records for dangerous recommendations.
It still does not execute real dangerous actions; approve only simulates execution.

## Next Stage

V1-P5: eval and real stress/smoke scripts.

- Add fixture eval cases.
- Add live smoke eval.
- Add bounded CPU / memory / GPU stress scripts.
