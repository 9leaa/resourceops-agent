# ResourceOps Agent

ResourceOps Agent is a local-first resource diagnosis agent for GPU, CPU, and Memory problems. It is based on the IncidentOps harness shape, but the product scope is real local resource diagnosis rather than simulated service incidents.

Current status: **V1-P7.5**.

## What Works Now

- Resource-focused schemas:
  - `ResourceIncident`
  - `DiagnosisRun`
  - `DiagnosisStep`
  - `ToolCall`
  - `EvidenceItem`
  - `DiagnosisFinding`
  - `Recommendation`
  - `Approval`
- CLI command renamed to `diagnose`.
- FastAPI endpoint renamed to `/diagnose`.
- Real local tools for CPU, memory, GPU, OOM lookup, and process inspection.
- Deterministic GPU / CPU / Memory / Mixed resource plans.
- ResourceAgent executes planned tools through ToolRegistry.
- Detectors convert tool results into `EvidenceItem` and `DiagnosisFinding` records.
- Reports include resource checks, key evidence, findings, recommendations, and tool errors.
- Dangerous recommendations create Approval records.
- Runs with pending approvals enter `waiting_approval` status.
- Approval records are persisted to `var/approvals.jsonl` and trace.
- Fixture eval with deterministic tool-output fixtures.
- Live smoke eval against the current machine.
- Bounded CPU / Memory / GPU stress scripts.
- Complete FastAPI demo flow for diagnose, runs, trace, approvals, approve, and reject.
- CLI approval and rejection commands synchronize approval/run status back to SQLite trace.
- CLI trace text output shows approval status.
- Dockerfile and Docker Compose local HTTP startup.
- ToolRegistry with permission levels, validation, timeout, preview, and summary fields.
- Approval store/service with simulated dangerous-action execution.
- SQLite TraceStore for runs, steps, tool calls, evidence items, findings, and approvals.
- Per-run workspace directories under `var/runs/<run_id>/`.
- Structured `ResourceAgentResult` shared by Agent, CLI, API, and TraceStore.
- Optional `llm_report` mode that rewrites only the final report from existing evidence, findings, recommendations, and approvals.
- Bounded report context builder gives LLM richer but controlled tool details such as top processes, GPU memory, memory/swap metrics, and OOM event previews.

V1-P7.5 still does not execute real dangerous actions. Approval only simulates execution after a human approve command.

## Quick Start

```bash
cd /home/zcj/resourceops-agent
python main.py diagnose "为什么 CPU 很高？"
```

The command executes a deterministic resource plan, runs detectors, creates approvals for dangerous recommendations, writes a trace to `var/resourceops.sqlite3`, and prints a V1-P7.5 diagnosis report.

Run with optional LLM report rewriting:

```bash
python main.py diagnose "为什么 CPU 很高？" --agent-mode llm_report
```

LLM settings are read from `.env` or environment variables:

```bash
RESOURCEOPS_LLM_BASE_URL=http://127.0.0.1:3000/v1
RESOURCEOPS_LLM_API_KEY=replace-with-your-key
RESOURCEOPS_LLM_MODEL=replace-with-your-model
```

In `llm_report` mode, trace includes `build_report_context` and `llm_report` steps. The first records the compact context given to the LLM, and the second records whether LLM generation succeeded or fell back.

Show recent runs:

```bash
python main.py runs
```

Show a trace:

```bash
python main.py trace <run_id>
```

Run fixture eval:

```bash
python eval/run_eval.py
```

Run live smoke eval:

```bash
python eval/run_live_smoke.py
```

Run bounded stress scripts manually:

```bash
python scripts/stress_cpu.py --duration 10 --workers 2
python scripts/stress_memory.py --mb 256 --duration 10
python scripts/stress_gpu_memory.py --mb 512 --duration 10 --yes
```

Run the API:

```bash
uvicorn app.api:app --host 0.0.0.0 --port 18000
```

Or run with Docker Compose:

```bash
docker compose up --build
```

HTTP diagnose:

```bash
curl -sS -X POST http://localhost:18000/diagnose \
  -H 'content-type: application/json' \
  -d '{"description":"为什么 GPU 显存满了？","resource_type":"gpu"}'
```

Full HTTP demo flow:

```bash
RUN_ID=$(
  curl -sS -X POST http://localhost:18000/diagnose \
    -H 'content-type: application/json' \
    -d '{"description":"为什么内存快满了？","resource_type":"memory"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["run"]["run_id"])'
)

curl -sS http://localhost:18000/runs
curl -sS http://localhost:18000/runs/$RUN_ID
curl -sS http://localhost:18000/approvals
```

If the diagnosis creates an approval, approve or reject it:

```bash
APPROVAL_ID=$(
  curl -sS http://localhost:18000/approvals \
  | python -c 'import json,sys; data=json.load(sys.stdin); print(data[0]["approval_id"] if data else "")'
)

curl -sS -X POST http://localhost:18000/approvals/$APPROVAL_ID/approve
curl -sS http://localhost:18000/runs/$RUN_ID
```

## Project Layout

```text
resourceops-agent/
├── app/       # CLI, FastAPI, schemas
├── agent/     # ResourceAgent and planning
├── tools/     # ToolRegistry and real resource tools
├── approval/  # Human approval store/service
├── trace/     # SQLite trace store
├── eval/      # fixture and live smoke eval
├── scripts/   # bounded stress scripts
├── tests/     # tool, planner, detector, agent, trace, API tests
└── var/       # Runtime state, ignored by git in a future repo
```

## 后续路线

当前已完成到 **V1-P8**。后续路线分两层：V1 先把单 Agent 做成“可控的 LLM 工具使用 Agent”，V2 再扩展成完整 Agent Harness。

V1-P8 已完成：

- `ToolRegistry` 可以导出结构化 `ToolCatalog`，说明有哪些工具、参数 schema、权限等级、标签和适用资源类型。
- deterministic planner 的固定计划已升级为 `ToolPlan` / `PlannedToolCall`。
- `ResourceAgent` 现在按 `ToolPlan.steps` 执行工具，诊断行为与 P7.5 保持一致。
- trace 普通视图和 JSON 视图都能看到本次使用的工具计划。

### V1 后续

- V1-P9：LLM Planner + PlanValidator。LLM 可以提出工具调用计划，但必须经过系统校验；非法计划 fallback 到 deterministic plan。
- V1-P10：TodoWrite / 任务面板。把 plan 转成可展示、可追踪、可恢复的任务列表。
- V1-P11：Workspace Isolation 增强。把 plan、todos、raw tool outputs、compact context、report 都保存到 `var/runs/<run_id>/`。

### V2 方向

- V2-P1：Hooks 和 Error Recovery，在关键流程节点插入安全、审计、恢复逻辑。
- V2-P2：Skills，让 planner 按场景加载 GPU OOM、CPU bottleneck、memory leak 等诊断技能。
- V2-P3：Memory 和基线，记录机器基线、历史问题、常见安全进程和用户偏好。
- V2-P4：Subagents，把 GPU / CPU / Memory / Process / Report 拆成独立子 Agent。
- V2-P5：Agent Team，由 LeadResourceAgent 协调多个专职 Agent 完成诊断。
- V2-P6：Background Tasks，支持 60 秒采样、内存增长观察、GPU 利用率趋势分析。
- V2-P7：Autonomous Resource Monitor Agent，后台发现异常并自动创建诊断任务，但危险动作仍必须审批。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle，支持 replay、debug bundle 和多 Agent 上下文隔离。
