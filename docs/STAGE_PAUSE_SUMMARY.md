# Stage Pause Summary

Current stage: **V1-P13 complete**

## Implemented

- Resource-focused schemas for incidents, runs, steps, tool calls, evidence, findings, recommendations, and approvals.
- Real local tools for CPU, memory, GPU, OOM lookup, and process inspection.
- Deterministic GPU / CPU / Memory / Mixed plans.
- ResourceAgent executes planned tools through ToolRegistry.
- Detectors convert tool results into `EvidenceItem` and `DiagnosisFinding` records.
- Reports include resource checks, key evidence, findings, recommendations, and tool errors.
- Dangerous recommendations create Approval records.
- Runs with pending approvals enter `waiting_approval` status.
- Fixture eval with deterministic tool-output fixtures.
- Live smoke eval against the current machine.
- Bounded CPU / Memory / GPU stress scripts.
- Complete FastAPI demo flow for diagnose, runs, trace, approvals, approve, reject, and execute-real.
- Approval decisions made through HTTP are synchronized back to SQLite trace.
- Approval decisions made through CLI are synchronized back to SQLite trace.
- Dockerfile and Docker Compose local HTTP startup.
- TraceStore persists runs, steps, tool calls, evidence items, findings, and approvals.
- CLI and FastAPI diagnosis flow.
- CLI trace output shows approval status in the normal text view.
- `ResourceAgentResult` is a structured schema object shared by Agent, CLI, API, and TraceStore.
- Diagnosis run summaries use a cleaner findings/evidence/approval count format.
- Optional `llm_report` mode rewrites only `final_report` from existing deterministic evidence, findings, recommendations, and approvals.
- LLM configuration is read from `.env` or environment variables, including local ccswitch/OpenAI-compatible base URLs.
- LLM report failures fallback to the deterministic template report.
- Trace records an `llm_report` step with LLM usage status, fallback reason, prompt/response lengths, and response preview.
- `build_report_context` creates bounded, redacted tool context for LLM reports and records it as a trace step.
- `ToolRegistry` is exposed as a structured `ToolCatalog` for planner / future LLM planner use.
- Deterministic plans are wrapped as `ToolPlan` / `PlannedToolCall`.
- `ResourceAgent` executes tools from `ToolPlan.steps`.
- Trace records a `build_tool_plan` step containing the tool plan and tool catalog snapshot.
- CLI trace text output shows the planned tools without requiring JSON trace inspection.
- `llm_planner` mode lets an LLM propose a candidate `ToolPlan` from the `ToolCatalog`.
- `PlanValidator` validates LLM plans before execution: tool existence, input args, step budget, duplicate calls, and write/dangerous/approval-required tools.
- Invalid, failed, or unavailable LLM planner calls fallback to the deterministic plan.
- Trace records an `llm_planner` step with prompt/response lengths, candidate plan, validation errors, fallback reason, and selected plan.
- ToolPlan steps are converted into persistent `DiagnosisTodo` tasks.
- TraceStore persists run phases and task todos.
- CLI trace text output shows layered todos.
- Rich Live CLI panel shows ResourceOps Agent phases and retained Tool execution / Approval / Action execution task details.
- Approval tasks are synchronized after approve/reject, including approval phase and run status.
- `--interactive-approval` lets CLI users handle pending approvals in the same terminal with y/n/s/q decisions.
- Interactive approval pauses Rich Live while reading input, uses colored prompts, then refreshes the final todo panel.
- P13 real action execution is available only through explicit `execute-real` entrypoints and remains disabled by default.
- `kill_process` real execution requires `RESOURCEOPS_ENABLE_REAL_ACTIONS=true`, allowlist, approved action, successful dry-run, pre-check, post-check, and explicit confirmation.
- `renice_process` is now supported as a write-level real action with pid/nice validation, dry-run, pre-check, post-check, and the same explicit execute-real boundary.
- `inspect_process` is now supported as a safe read-only action surface without approval, env enablement, allowlist, or system-state change.
- HTTP `POST /approvals/{approval_id}/execute-real` records real-mode `ActionResult` into trace/workspace.
- P13 tests include mocked process execution and a live smoke test that terminates only a child process created by the test.

## Verified

```bash
python -m compileall -q actions app agent approval trace tools scripts eval tests workspace
conda run -n zcj_hello python -m pytest -q
```

Latest local result after P13.4: targeted P13/API/CLI suite passed at `40 passed`; full suite pending in the current run if not listed below.

## Current Boundary

V1-P13.2 creates approval records for dangerous recommendations and supports complete HTTP, CLI command, interactive approval, dry-run action execution, and gated real action execution trace synchronization.
`approve` still only performs dry-run. Real execution requires explicit `execute-real`, env enablement, allowlist, approval, dry-run, pre-check, post-check, and confirmation.
LLM planner can choose safe diagnostic tools only after validation.
LLM still cannot call tools directly, create findings, bypass approval, or change run status.
LLM report mode remains separate and only rewrites the final report.
Todo state is a UI/trace layer and does not change detector or tool execution semantics.

## Next Stages

Next: **V2-P1 Hooks and Error Recovery**。

- V1-P11：Workspace Isolation 增强已完成，保存 plan、todos、raw tool outputs、compact context、report 和 debug bundle。
- V1-P12：Action Executor dry-run 已完成。approve 后生成 ActionResult(mode=dry_run)，并同步 trace、todo、workspace、CLI/API。
- V1-P13：真实安全动作执行已完成。首批 action surface 为 `inspect_process` safe read-only、`renice_process` write-level gated real action、`kill_process` dangerous gated real action。

V2 方向：

- V2-P1：Hooks 和 Error Recovery。
- V2-P2：Skills。
- V2-P3：Memory 和机器基线。
- V2-P4：Subagents。
- V2-P5：Agent Team。
- V2-P6：Background Tasks。
- V2-P7：Autonomous Resource Monitor Agent。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle。
