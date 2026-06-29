# ResourceOps Agent Design

The long-form design is currently maintained at:

```text
/home/zcj/resourceops-agent/ResourceOps_Agent_DESIGN.md
```

This project implements that design as a separate ResourceOps codebase.

Current implementation stage: **V1-P10.8 Interactive Approval**.

Implemented through V1-P10.8:

- V1-P0: project rename and schema adjustment.
- V1-P1: real local GPU / CPU / Memory / Process tools.
- V1-P2: deterministic ResourceAgent plans and ToolRegistry execution.
- V1-P3: detectors that produce `EvidenceItem` and `DiagnosisFinding` records from tool results.
- V1-P4: dangerous recommendations create Approval records and runs enter `waiting_approval`.
- V1-P5: fixture eval, live smoke eval, and bounded CPU / Memory / GPU stress scripts.
- V1-P6: complete FastAPI demo flow, approval trace synchronization, and Docker Compose startup.
- V1-P6.5: CLI approval trace synchronization, structured `ResourceAgentResult`, trace display polish, and report summary cleanup.
- V1-P7: optional LLM report writer that rewrites only `final_report` from existing deterministic evidence and approvals.
- V1-P7.5: bounded report context builder and trace step for the compact context given to the LLM.
- V1-P8: ToolCatalog and ToolPlan schema. Deterministic plans now run through a structured `ToolPlan`, and trace records the plan used for each run.
- V1-P9: LLM Planner + PlanValidator. `llm_planner` mode lets an LLM propose a tool plan, then validates tool names, args, permissions, step budget, and falls back safely.
- V1-P10: TodoWrite / 任务面板基础版。ToolPlan 会转换为可追踪 todos，CLI/trace 能看到最终任务状态。
- V1-P10.5: 分层任务面板。Run 被拆成 Planning tools、Tool execution、Report、Approval、Action execution 等大任务。
- V1-P10.6: Rich Live CLI 面板。非 JSON 诊断模式下用刷新式终端面板展示大任务和保留式任务详情。
- V1-P10.7: Approval / Action execution 阶段展示和 trace 同步。审批 task 会随 approve/reject 更新状态。
- V1-P10.8: Interactive Approval。CLI diagnose 可选 `--interactive-approval`，支持 y/n/s/q 逐个处理 pending approvals。

Next stage: **V1-P11 Workspace Isolation 增强**.

V1 后续路线：

- V1-P10：TodoWrite / 任务面板，把计划变成可追踪任务。
- V1-P10.5：分层任务面板 / Live Todo UI，把 run 拆成 Planning tools、Tool execution、Report、Approval、Action execution 等大任务，并用刷新式 CLI 面板展示任务详情。
- V1-P10.6：Rich Live 刷新式 CLI 面板，避免多次 print 堆叠输出。
- V1-P10.7：审批任务进入 todo/trace，approve/reject 后同步 approval task、phase 和 run.status。
- V1-P10.8：交互审批入口。默认异步不阻塞，显式 `--interactive-approval` 后批量列出并逐个审批。
- V1-P11：Workspace Isolation 增强，保存 plan、todos、raw、compact、report 等运行产物。
- V1-P12：Action Executor dry-run，定义可审批动作执行器，但只模拟执行和记录 pre-check / post-check。
- V1-P13：真实安全动作执行，只开放白名单动作，必须 approval、pre-check、dry-run、post-check 全部通过。

V2 路线：

- V2-P1：Hooks 和 Error Recovery。
- V2-P2：Skills。
- V2-P3：Memory 和机器基线。
- V2-P4：Subagents。
- V2-P5：Agent Team。
- V2-P6：Background Tasks。
- V2-P7：Autonomous Resource Monitor Agent。
- V2-P8：Workspace Isolation 完整化和 Debug Bundle。
