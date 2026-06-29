# Stage Pause Summary

Current stage: **V1-P10.8**

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
- Complete FastAPI demo flow for diagnose, runs, trace, approvals, approve, and reject.
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

## Verified

```bash
python -m compileall -q app agent approval trace tools scripts eval tests
conda run -n zcj_hello python -m pytest -q
```

Latest local result after P10.8: `79 passed`; fixture eval previously passed at `4/4`; live smoke previously passed.

## Current Boundary

V1-P10.8 creates approval records for dangerous recommendations and supports complete HTTP, CLI command, and interactive CLI approval trace synchronization.
It still does not execute real dangerous actions; approve only simulates execution.
LLM planner can choose safe diagnostic tools only after validation.
LLM still cannot call tools directly, create findings, bypass approval, or change run status.
LLM report mode remains separate and only rewrites the final report.
Todo state is a UI/trace layer and does not change detector or tool execution semantics.

## Next Stages

Next: **V1-P11 Workspace Isolation 增强**。

- V1-P11：Workspace Isolation 增强，保存 plan、todos、raw tool outputs、compact context 和 report。
- V1-P12：Action Executor dry-run，定义动作执行器边界，但只模拟执行和记录检查结果。
- V1-P13：真实安全动作执行，只允许白名单动作，必须 approval、pre-check、dry-run、post-check 全部通过。

V2 方向：

- V2-P1：Hooks 和 Error Recovery。
- V2-P2：Skills。
- V2-P3：Memory 和机器基线。
- V2-P4：Subagents。
- V2-P5：Agent Team。
- V2-P6：Background Tasks。
- V2-P7：Autonomous Resource Monitor Agent。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle。
