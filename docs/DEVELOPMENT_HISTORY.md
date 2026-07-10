# Development History

This document keeps the detailed implementation history that used to live in the README. The README is intentionally product-facing; this file preserves the stage-by-stage engineering record.

## Current Boundary

ResourceOps Agent is complete through the local MVP line: deterministic diagnosis, bounded LLM planning/reporting, approval-gated dry-run actions, gated real action execution, trace/workspace persistence, debug bundle export, CLI, FastAPI, and eval fixtures.

`approve` still only runs `ActionExecutor` dry-run. Real execution is only available through explicit `execute-real` entrypoints and remains disabled unless `RESOURCEOPS_ENABLE_REAL_ACTIONS=true` and the action is allowlisted.

LLM planner cannot call tools directly. It only proposes a plan that must pass validation.

## Implemented Capabilities

- Resource-focused schemas:
  - `ResourceIncident`
  - `DiagnosisRun`
  - `DiagnosisStep`
  - `ToolCall`
  - `EvidenceItem`
  - `DiagnosisFinding`
  - `Recommendation`
  - `Approval`
  - `ActionResult`
- CLI command renamed to `diagnose`.
- FastAPI endpoint renamed to `/diagnose`.
- Real local tools for CPU, memory, GPU, OOM lookup, and process inspection.
- Deterministic GPU / CPU / Memory / Mixed resource plans.
- `ResourceAgent` executes planned tools through `ToolRegistry`.
- Detectors convert tool results into `EvidenceItem` and `DiagnosisFinding` records.
- Reports include resource checks, key evidence, findings, recommendations, approvals, and tool errors.
- Dangerous recommendations create approval records.
- Runs with pending approvals enter `waiting_approval` status.
- Approval records are persisted to `var/approvals.jsonl` and trace.
- Fixture eval with deterministic tool-output fixtures.
- Live smoke eval against the current machine.
- Bounded CPU / Memory / GPU stress scripts.
- Complete FastAPI demo flow for diagnose, runs, trace, approvals, approve, reject, and execute-real.
- CLI approval and rejection commands synchronize approval/run status back to SQLite trace.
- CLI trace text output shows approval status.
- Dockerfile and Docker Compose local HTTP startup.
- `ToolRegistry` includes permission levels, validation, timeout, preview, and summary fields.
- Approval store/service integrates with `ActionExecutor` dry-run and gated real execution for approved actions.
- SQLite `TraceStore` persists runs, steps, tool calls, evidence items, findings, approvals, todos, and action results.
- Per-run workspace directories are written under `var/runs/<run_id>/`.
- Workspace files include metadata, plan, todos, report, raw tool outputs, compact report context, trace artifacts, approvals, and action results.
- `workspace <run_id>` inspects a run workspace.
- `bundle <run_id>` exports a debug bundle.
- Structured `ResourceAgentResult` is shared by Agent, CLI, API, and TraceStore.
- Optional LLM report mode rewrites only the final report from existing evidence, findings, recommendations, and approvals.
- Bounded report context builder gives LLM richer but controlled tool details such as top processes, GPU memory, memory/swap metrics, and OOM event previews.
- `ToolCatalog` and structured `ToolPlan` / `PlannedToolCall` exist for every diagnosis run.
- Optional LLM planner mode lets an LLM propose a tool plan; `PlanValidator` checks it, and invalid plans fall back to deterministic planning.
- TodoWrite-style task tracking for tool plans is persisted in trace.
- Rich Live CLI task panel shows run phases and retained Tool execution / Approval / Action execution details.
- Optional interactive approval flow with `--interactive-approval`, including colored approval prompts and `y/r/n/s/q` decisions.
- Approved dangerous recommendations create `ActionResult(mode=dry_run)` records and update Action execution todos.
- Gated real action execution is available through `execute-real` and `POST /approvals/{approval_id}/execute-real`.
- P13 added `renice_process` as a write-level real action using the same gated executor path as `kill_process`.
- P13 added `inspect_process` as a safe read-only action surface.

## Stage Summary

### V1-P0 to V1-P7.5

- P0: project rename and resource-focused schema adjustment.
- P1: real local GPU / CPU / Memory / Process tools.
- P2: deterministic `ResourceAgent` plans and `ToolRegistry` execution.
- P3: detectors that produce structured evidence and findings from tool results.
- P4: dangerous recommendations create approvals and runs enter `waiting_approval`.
- P5: fixture eval, live smoke eval, and bounded CPU / Memory / GPU stress scripts.
- P6: complete FastAPI demo flow, approval trace synchronization, and Docker Compose startup.
- P6.5: CLI approval trace synchronization, structured `ResourceAgentResult`, trace display polish, and report summary cleanup.
- P7: optional LLM report writer that rewrites only `final_report` from existing deterministic evidence and approvals.
- P7.5: bounded report context builder and trace step for compact context given to the LLM.

### V1-P8: ToolCatalog and ToolPlan

- `ToolRegistry` exports structured `ToolCatalog` metadata.
- Deterministic plans are wrapped as `ToolPlan` / `PlannedToolCall`.
- `ResourceAgent` executes tools from `ToolPlan.steps`.
- Trace records the selected tool plan and catalog snapshot.

### V1-P9: LLM Planner and PlanValidator

- `llm_planner` mode lets an LLM propose a candidate `ToolPlan` from the `ToolCatalog`.
- `PlanValidator` validates tool existence, input args, step budget, duplicate calls, resource type, and permission boundary.
- Invalid, failed, or unavailable LLM planner calls fall back to deterministic planning.
- Trace records prompt/response lengths, candidate plan, validation errors, fallback reason, and selected plan.

### V1-P10: Todo Panel and Interactive Approval

- `ToolPlan` steps are converted into persistent `DiagnosisTodo` tasks.
- TraceStore persists run phases and task todos.
- CLI trace text output shows layered todos.
- Rich Live CLI panel shows ResourceOps Agent phases and retained Tool execution / Approval / Action execution task details.
- Approval tasks synchronize after approve/reject, including approval phase and run status.
- `--interactive-approval` lets CLI users handle pending approvals in the same terminal with `y/r/n/s/q` decisions.
- Interactive approval pauses Rich Live while reading input, uses colored prompts, then refreshes the final todo panel.

### V1-P11: Workspace Isolation and Debug Bundle

- P11.1: Workspace writer persists metadata, plan, todos, report, tool outputs, steps, evidence, findings, and approvals.
- P11.2: Compact report context is saved to `compact/report_context.json`.
- P11.3: CLI workspace viewer supports `workspace <run_id>`, `--json`, `--show-report`, and `--show-context`.
- P11.4: approve/reject/interactive approval update workspace approvals, todos, and metadata.
- P11.5: debug bundle export writes `var/bundles/<run_id>.tar.gz`.

### V1-P12: Action Executor Dry-Run

- Approving an action creates `ActionResult(mode=dry_run)`.
- Action results synchronize into trace, todos, workspace, CLI, and API.
- Dry-run records pre-check, execution preview, post-check, status, and errors.

### V1-P13: Gated Real Actions

- `execute-real` is the only real execution entrypoint.
- Real execution is disabled by default.
- Real execution requires env enablement, action allowlist, approval, successful dry-run, pre-check, post-check, and explicit confirmation.
- `kill_process` is supported as a dangerous gated action.
- `renice_process` is supported as a write-level gated action.
- `inspect_process` is supported as a safe read-only action.

## Verified Commands

```bash
python -m compileall -q actions app agent approval trace tools scripts eval tests workspace
python -m pytest -q
python eval/run_eval.py
```

Local development often used:

```bash
conda run -n zcj_hello python -m pytest -q
conda run -n zcj_hello python eval/run_eval.py
```

## Detailed Design References

- [DESIGN.md](DESIGN.md)
- [ResourceOps_Agent_DESIGN.md](ResourceOps_Agent_DESIGN.md)
- [STAGE_PAUSE_SUMMARY.md](STAGE_PAUSE_SUMMARY.md)
