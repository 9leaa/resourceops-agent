# ResourceOps Agent Design

The long-form design is currently maintained at:

```text
/home/zcj/resourceops-agent/ResourceOps_Agent_DESIGN.md
```

This project implements that design as a separate ResourceOps codebase.

Current implementation stage: **V1-P6.5**.

Implemented through V1-P6.5:

- V1-P0: project rename and schema adjustment.
- V1-P1: real local GPU / CPU / Memory / Process tools.
- V1-P2: deterministic ResourceAgent plans and ToolRegistry execution.
- V1-P3: detectors that produce `EvidenceItem` and `DiagnosisFinding` records from tool results.
- V1-P4: dangerous recommendations create Approval records and runs enter `waiting_approval`.
- V1-P5: fixture eval, live smoke eval, and bounded CPU / Memory / GPU stress scripts.
- V1-P6: complete FastAPI demo flow, approval trace synchronization, and Docker Compose startup.
- V1-P6.5: CLI approval trace synchronization, structured `ResourceAgentResult`, trace display polish, and report summary cleanup.

Next stage: **V1-P7 LLM 报告生成器**.

V1 后续路线：

- V1-P7：LLM 只改写报告，不参与工具选择和危险决策。
- V1-P8：工具目录和计划 schema，为 LLM 工具规划做准备。
- V1-P9：LLM Planner + PlanValidator，让 LLM 提出计划，系统负责校验、执行、审批和 trace。
- V1-P10：TodoWrite / 任务面板，把计划变成可追踪任务。
- V1-P11：Workspace Isolation 增强，保存 plan、todos、raw、compact、report 等运行产物。

V2 路线：

- V2-P1：Hooks 和 Error Recovery。
- V2-P2：Skills。
- V2-P3：Memory 和机器基线。
- V2-P4：Subagents。
- V2-P5：Agent Team。
- V2-P6：Background Tasks。
- V2-P7：Autonomous Resource Monitor Agent。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle。
