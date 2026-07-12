# ResourceOps Agent：审批流程与 LLM Report 解耦方案

> 目标：Approval 一旦由确定性 Detector 生成，就可以立即展示并处理，不再等待 LLM Report 返回。
> 适用仓库：`https://github.com/9leaa/resourceops-agent`

---

## 1. 当前问题

当前流程：

```text
Tool 执行
→ Detector 生成 Evidence / Finding
→ 创建 Approval Todo
→ 调用 LLM Report
→ diagnose() 返回
→ CLI 才进入审批交互
```

因此会出现：

- Todo 已显示 `waiting_approval`；
- Approval 已经存在；
- 但用户必须等待 LLM Report 完成后才能操作；
- 当 Report 延迟达到 90 秒以上时，审批入口被无意义阻塞。

根本问题：

```text
Approval 已经 ready
但交互入口依赖 diagnose() 完整结束
```

Approval 的依据来自：

```text
Evidence
Finding
Recommendation
Risk
Action args
```

不依赖 LLM Report。

---

# 2. 正确依赖关系

应该改为：

```text
Evidence + Finding
        ├──→ Approval → 用户可立即审批
        └──→ Report Context → LLM Report
```

而不是：

```text
Evidence + Finding
→ LLM Report
→ 用户审批
```

LLM Report 失败、超时或回退时，不应影响审批链。

---

# 3. 推荐实现方案

## 3.1 第一阶段：明确状态，不做并发

先做低风险改造。

新增 Todo 状态：

```text
approval_detected
```

Report 完成后再改为：

```text
waiting_approval
```

这样不会让 UI 在实际不可操作时提前显示成“可审批”。

---

## 3.2 第二阶段：Approval 立即持久化

修改 `ResourceAgent.diagnose()` 的阶段边界。

当前在 Detector 后只创建内存中的 Approval，直到整个 `diagnose()` 结束后才统一写入 Trace。

改为：

```text
Detector 完成
→ 创建 Approval
→ 立即写入 ApprovalStore
→ 立即写入 TraceStore
→ 通知 EventSink
→ Report 继续生成
```

建议新增事件：

```python
class AgentEventSink(Protocol):
    def on_approval_created(self, approval: Approval) -> None:
        ...

    def on_approval_ready(self, approval: Approval) -> None:
        ...
```

EventSink 收到事件后立即展示审批卡。

---

## 3.3 第三阶段：Report 与审批交互并行

推荐 CLI 流程：

```text
诊断工具执行完成
→ Detector 生成 Approval
→ CLI 立即进入审批交互
→ LLM Report 同时生成
```

当前交互式 CLI 采用“后台 stream + 前台暂存”：

```text
1. Tool execution 完成；
2. 立即刷新 approval 卡片，用户可以批准、真实执行、拒绝、跳过或退出；
3. 用户处理 approval 期间，LLM Report 在后台线程 streaming；
4. streaming chunk 只写入内存 buffer，不打断 input()；
5. 用户处理完审批后，等待后台 report 完成；
6. 完整 report 校验通过后，用最新 Trace 重写 `审批状态` 和 `风险说明`；
7. 将最终 report 写入 Trace 和 Workspace；
8. 刷新 Live todo 面板，让 `Report` phase 进入 completed/failed 最终态；
9. 关闭 Live todo 面板，再打印最终 report。
```

这样做的边界：

- Planner 不 streaming，因为 planner 输出是结构化计划，必须完整返回后才能校验和执行；
- Report 可以 streaming，因为它是人类阅读的 Markdown；
- 前台审批输入期间不直接打印 chunk，避免 Rich Live 和 `input()` 输出互相打断；
- LLM 输出中的写作说明、开场白会在最终报告中裁剪，只保留从 `问题概览` 开始的正文；
- `审批状态` 和 `风险说明` 属于动态状态，不信任 LLM 生成时的旧快照，最终展示和落盘前必须从最新 Trace / ActionResult 确定性重写。
- Live 面板不能在 report 落盘前关闭，否则终端会固定显示 `Report running` 的旧快照。

### 方案 A：线程并发

使用：

```python
concurrent.futures.ThreadPoolExecutor
```

示意：

```python
with ThreadPoolExecutor(max_workers=1) as executor:
    stream_buffer = ReportStreamBuffer()
    report_future = executor.submit(
        build_llm_report_result,
        ...,
        stream_callback=stream_buffer.append,
    )

    run_interactive_approvals(...)

    print_buffered_report(stream_buffer)
    llm_report_result = report_future.result()
```

适合当前同步代码结构，改动较小。

### 方案 B：阶段式接口

将 `diagnose()` 拆成：

```python
diagnosis = agent.collect_and_detect(incident)
report = agent.generate_report(diagnosis)
```

CLI：

```python
diagnosis = agent.collect_and_detect(incident)
persist_diagnosis(diagnosis)
show_approvals(diagnosis.approvals)

report = agent.generate_report(diagnosis)
persist_report(report)
```

长期推荐方案 B，结构更清楚。

---

# 4. 推荐的数据模型

新增中间结果：

```python
class DiagnosisSnapshot(StrictBaseModel):
    run: DiagnosisRun
    incident: ResourceIncident
    tool_plan: ToolPlan
    tool_results: list[ToolExecutionResult]
    evidence_items: list[EvidenceItem]
    findings: list[DiagnosisFinding]
    approvals: list[Approval]
    todos: list[DiagnosisTodo]
    steps: list[DiagnosisStep]
```

新增报告结果：

```python
class ReportSnapshot(StrictBaseModel):
    run_id: str
    final_report: str
    report_mode: str
    source: str
    latency_ms: int
    llm_call: dict | None
```

职责：

```text
DiagnosisSnapshot
= 审批所需的全部确定性数据

ReportSnapshot
= 后续生成的展示文本
```

---

# 5. CLI 行为

修改后推荐输出顺序：

```text
✓ Tool execution completed
✓ Findings generated
! Approval ready

Finding: memory_process_hogging
Evidence: PID 1234 uses 44.7% memory
Action: kill_process
Risk: dangerous

y / r / n / s
```

Report 状态单独显示：

```text
Report: generating
Report: completed
```

审批卡不得依赖自然语言报告。

## 5.1 Report 与审批状态一致性

并发后会出现一个新边界：LLM report 线程启动时，prompt 里看到的 approval
可能还是 `pending`；用户随后可能立刻批准、拒绝、dry-run 或真实执行。

因此最终规则是：

```text
LLM report
= 诊断解释正文

Trace / ApprovalStore / ActionResult
= 审批与执行状态的唯一可信来源
```

CLI 在输出和落盘前必须执行 reconcile：

```text
删除 LLM 生成的旧 `审批状态` / `风险说明`
→ 从最新 trace.approvals 和 trace.action_results 生成新章节
→ 保存最终 report.md
→ 刷新 todos.json / metadata.json / trace artifacts
```

同样地，如果用户在 diagnose 命令结束后，再通过 `approve` / `reject` /
`execute-real` 改变审批或动作状态，也必须重新执行同一套 reconcile：

```text
审批状态变化
→ 更新 ApprovalStore / TraceStore
→ 重写 diagnosis_runs.final_report 的动态章节
→ WorkspaceWriter.update_from_trace() 重写 report.md / todos.json / metadata.json
```

预期效果：

```text
如果用户在 report 生成期间执行了真实 kill：
最终报告必须显示 real=success preview=real: terminated pid=...
不能继续显示 approval_status=pending 或 “尚未执行”
```

---

# 6. API 行为

诊断 API 可以返回两阶段状态：

```json
{
  "run_id": "run_xxx",
  "diagnosis_status": "completed",
  "report_status": "generating",
  "approvals": [
    {
      "approval_id": "appr_xxx",
      "status": "pending"
    }
  ]
}
```

后续查询：

```http
GET /runs/{run_id}
GET /runs/{run_id}/report
GET /runs/{run_id}/approvals
```

---

# 7. 安全约束

必须保持：

1. Approval 只能由确定性 Finding/Recommendation 创建；
2. LLM 不能直接创建可执行 Approval；
3. `kill_process` 等危险操作仍必须人工确认；
4. real execution 仍需：
   - 环境变量开启；
   - allowlist；
   - dry-run；
   - `confirm_real=True`；
   - pre-check；
   - post-check；
5. Report 超时、失败或回退不得自动批准任何操作。

---

# 8. 文件修改清单

## 重点修改

```text
agent/resource_agent.py
app/cli.py
app/schemas.py
trace/store.py
workspace/writer.py
approval/service.py
approval/trace_sync.py
```

## 推荐新增

```text
agent/diagnosis_snapshot.py
agent/report_service.py
tests/test_approval_report_decoupling.py
tests/test_interactive_approval_before_report.py
tests/test_report_failure_does_not_block_approval.py
```

---

# 9. 实施顺序

## Phase 1：状态修正

- Approval Todo 在 Report 期间显示为 `approval_detected`；
- Report 完成后变为 `waiting_approval`；
- 不改变整体同步流程。

## Phase 2：拆分诊断与报告

- 新增 `DiagnosisSnapshot`；
- 将 `collect/detect/approval` 与 `generate_report` 分开；
- Diagnosis 完成后立即持久化。

## Phase 3：CLI 并发

- Report 放入后台线程；
- CLI 立即进入审批交互；
- Report 完成后更新 Workspace。

## Phase 4：API 状态化

- 增加 `report_status`；
- Approval API 不依赖报告完成。

---

# 10. 测试要求

必须增加：

```text
test_approval_is_created_before_llm_report_returns
test_pending_approval_can_be_read_while_report_is_generating
test_report_timeout_does_not_block_approval
test_report_failure_does_not_change_approval_status
test_reject_can_happen_before_report_finishes
test_dry_run_can_happen_before_report_finishes
test_real_action_still_requires_all_safety_gates
test_report_completion_updates_workspace_without_overwriting_action_state
```

建议使用阻塞型 Fake LLM：

```python
class BlockingReportClient:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def generate_report(self, prompt: str) -> str:
        self.started.set()
        self.release.wait(timeout=5)
        return VALID_REPORT
```

测试：

```text
Report 已开始但尚未返回
→ Approval 已存在
→ Trace 可查询
→ 用户可以 reject / dry-run
```

---

# 11. 验收标准

- [ ] Approval 创建后立即可见；
- [ ] 不需要等待 LLM Report；
- [ ] Report 失败不影响 Approval；
- [ ] Todo 状态与真实可操作状态一致；
- [ ] Report 和 Approval 可以独立更新；
- [ ] 原有安全门全部保留；
- [ ] SQLite 和 Workspace 状态最终一致；
- [ ] 所有现有测试继续通过。

---

# 12. 给 Codex 的执行要求

```text
请按 Phase 1 → Phase 4 分阶段实施。

约束：
1. 不允许让 LLM 直接创建或批准危险操作。
2. Approval 必须继续来自确定性 Finding。
3. 不削弱 ActionExecutor 的任何安全检查。
4. Diagnosis 和 Report 必须拆分为独立阶段。
5. Approval 状态必须能够在 Report 尚未完成时持久化和查询。
6. 优先使用 ThreadPoolExecutor，不要立即把整个项目重写成 asyncio。
7. 每个 Phase 完成后运行相关测试。
8. Report 完成后不得覆盖已发生的审批和动作状态。
```
