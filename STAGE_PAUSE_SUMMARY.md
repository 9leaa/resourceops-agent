# Stage Pause Summary

Current stage: **V1-P7.5**

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

## Verified

```bash
python -m compileall -q app agent approval trace tools scripts eval tests
conda run -n zcj_hello python -m pytest -q
```

Latest local result: `49 passed`; fixture eval passed at `4/4`; live smoke passed.

## Current Boundary

V1-P7.5 creates approval records for dangerous recommendations and supports complete HTTP and CLI approval trace synchronization.
It still does not execute real dangerous actions; approve only simulates execution.
LLM mode does not choose tools, call tools, create findings, or change approval/run status.

## Next Stages

Next: **V1-P8 工具目录和计划 schema**。

- V1-P8：工具目录和计划 schema，把 ToolRegistry 暴露成可给 LLM 使用的工具目录，并定义 ToolPlan。
- V1-P9：LLM Planner + PlanValidator，LLM 只提出计划，系统负责校验、执行、审批和 trace。
- V1-P10：TodoWrite / 任务面板，把 plan 转成可展示、可追踪、可恢复的任务列表。
- V1-P11：Workspace Isolation 增强，保存 plan、todos、raw tool outputs、compact context 和 report。

V2 方向：

- V2-P1：Hooks 和 Error Recovery。
- V2-P2：Skills。
- V2-P3：Memory 和机器基线。
- V2-P4：Subagents。
- V2-P5：Agent Team。
- V2-P6：Background Tasks。
- V2-P7：Autonomous Resource Monitor Agent。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle。
