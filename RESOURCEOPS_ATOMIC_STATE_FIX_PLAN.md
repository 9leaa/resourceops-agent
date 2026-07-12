# ResourceOps Agent 关键一致性问题修复方案

> 适用仓库：`https://github.com/9leaa/resourceops-agent`
> 目标提交建议：`Fix atomic approval and report state transitions`
>
> 本方案针对当前版本中仍存在的关键一致性问题：
>
> 1. Approval 在 DiagnosisSnapshot 事务之前提前写入；
> 2. ApprovalService 先提交状态，再保存 ActionResult/Todo/RunStatus；
> 3. ReportSnapshot 与 report_status 分两次提交；
> 4. Workspace 写入失败可能阻止后台 Report Job 启动；
> 5. 多 Uvicorn Worker 会错误地把其他进程的 Report Job 标记为失败。

---

# 一、总体目标

完成本方案后，系统必须满足以下原则：

```text
Approval 对象创建 ≠ Approval 状态持久化
```

```text
审批状态 + ActionResult + Todo + RunStatus
必须在一个数据库事务中提交
```

```text
Report 内容 + Report 状态
必须在一个数据库事务中提交
```

```text
SQLite 是唯一状态源
Workspace 只是可重建视图
```

最终流程：

```text
Diagnosis
→ 内存中创建 Approval
→ 原子保存 DiagnosisSnapshot
→ 返回 run_id / approvals
→ 后台生成 Report
→ 用户审批
→ 原子提交 Approval Transition
→ 原子提交 Report Finalization
→ Workspace 从 SQLite 刷新
```

---

# 二、问题 1：Approval 提前持久化

## 2.1 当前问题

当前 `ResourceAgent._create_approvals()` 调用：

```python
approval_service.request_approval(...)
```

而 `request_approval()` 会立即：

```python
store.save(approval)
```

因此真实顺序是：

```text
Approval 先写入 SQLite
→ save_diagnosis_snapshot() 再保存 Run/Steps/Findings/Todos
```

如果 DiagnosisSnapshot 保存失败，数据库可能出现：

```text
approvals 表有记录
diagnosis_runs 表没有对应完整 Run
```

形成孤立 Approval。

## 2.2 修改目标

`ResourceAgent` 诊断阶段只负责创建 Approval 对象，不直接保存。

真正持久化必须统一放入：

```python
TraceStore.save_diagnosis_snapshot()
```

的事务中。

## 2.3 修改 `approval/service.py`

新增纯构造方法：

```python
def build_approval(
    self,
    *,
    run_id: str,
    action: str,
    args: dict[str, Any],
    reason: str,
    risk: RiskLevel = RiskLevel.DANGEROUS,
) -> Approval:
    return Approval(
        run_id=run_id,
        action=action,
        args=args,
        reason=reason,
        risk=risk,
    )
```

将现有 `request_approval()` 改为兼容接口：

```python
def request_approval(..., persist: bool = True) -> Approval:
    approval = self.build_approval(
        run_id=run_id,
        action=action,
        args=args,
        reason=reason,
        risk=risk,
    )

    if persist:
        return self.store.save(approval)

    return approval
```

推荐最终逐步淘汰 `persist=True` 的隐式行为。

## 2.4 修改 `agent/resource_agent.py`

将：

```python
approval = self.approval_service.request_approval(...)
```

改为：

```python
approval = self.approval_service.build_approval(
    run_id=run_id,
    action=action.action,
    args=approval_args_from_recommendation(action),
    reason=action.reason,
    risk=action.risk,
)
```

或者：

```python
approval = self.approval_service.request_approval(
    ...,
    persist=False,
)
```

推荐使用 `build_approval()`，语义更明确。

## 2.5 保存位置

保持：

```python
TraceStore.save_diagnosis_snapshot()
```

中的：

```python
for approval_data in snapshot.approvals:
    self.save_approval(
        Approval.model_validate(approval_data),
        connection=connection,
    )
```

这样 Approval 与以下内容一次提交：

```text
run
steps
tool_calls
evidence
findings
approvals
todos
```

## 2.6 测试

新增测试：

```python
def test_collect_and_detect_does_not_persist_approval_before_snapshot_save():
    ...
```

流程：

1. 创建临时 SQLite；
2. 调用 `agent.collect_and_detect()`；
3. 不调用 `save_diagnosis_snapshot()`；
4. 确认 `approvals` 表没有记录；
5. 调用 `save_diagnosis_snapshot()`；
6. 确认 Approval 和 Run 同时存在。

再增加失败测试：

```python
def test_snapshot_failure_does_not_leave_orphan_approval():
    ...
```

故意让 `save_finding()` 抛异常，确认：

```text
diagnosis_runs = 0
approvals = 0
diagnosis_todos = 0
```

---

# 三、问题 2：Approval Transition 不是真正原子化

## 3.1 当前问题

当前审批流程：

```text
pending
→ ApprovalStore.update_status(approved)
→ 执行 ActionExecutor
→ ApprovalStore.update_status(executed)
→ sync_approval_trace()
→ 保存 ActionResult / Todo / RunStatus
```

前两次 Approval 更新已经独立提交。

因此即使后续 `apply_approval_transition()` 有事务，也只能保证后半段。

可能出现：

```text
Approval = approved
ActionResult = 不存在
Approval Todo = waiting_approval
RunStatus = waiting_approval
```

## 3.2 修改目标

审批状态转换必须由一个统一入口完成：

```text
读取 pending Approval
→ 校验并锁定状态
→ 执行 ActionExecutor
→ 更新 Approval
→ 保存 ActionResult
→ 更新 Todos
→ 更新 RunStatus
→ 更新 Report 动态章节
→ COMMIT
```

但注意：

```text
真实外部动作不能放在长时间 SQLite 事务中
```

否则数据库会长时间持有写锁。

所以推荐采用“两阶段状态机”：

```text
Phase A：原子 claim
pending → approved

Phase B：执行 ActionExecutor

Phase C：原子 finalize
approved → executed / failed
+ ActionResult
+ Todos
+ RunStatus
+ Report
```

## 3.3 增加条件状态更新

修改 `trace/store.py`，新增：

```python
def transition_approval_status(
    self,
    approval_id: str,
    *,
    expected_statuses: set[ApprovalStatus],
    next_status: ApprovalStatus,
    decided_at=None,
    executed_at=None,
    connection: sqlite3.Connection | None = None,
) -> Approval:
    ...
```

SQL：

```sql
UPDATE approvals
SET status = ?,
    decided_at = COALESCE(?, decided_at),
    executed_at = COALESCE(?, executed_at)
WHERE approval_id = ?
  AND status IN (...);
```

执行后必须检查：

```python
if cursor.rowcount != 1:
    raise ApprovalTransitionConflict(...)
```

新增异常：

```python
class ApprovalTransitionConflict(ValueError):
    pass
```

这样可以避免：

```text
两个请求同时 approve
→ 两次 ActionExecutor 执行
```

## 3.4 Approval Claim

新增：

```python
def claim_approval_for_dry_run(
    self,
    approval_id: str,
) -> Approval:
    return self.transition_approval_status(
        approval_id,
        expected_statuses={ApprovalStatus.PENDING},
        next_status=ApprovalStatus.APPROVED,
        decided_at=utc_now(),
    )
```

只有一个请求能成功把：

```text
pending → approved
```

其他并发请求必须得到 409 Conflict 或 ValueError。

## 3.5 Finalize Approval Transition

新增：

```python
def finalize_approval_action(
    self,
    *,
    approval_id: str,
    action_result: ActionResult,
) -> Approval:
    with self.transaction() as connection:
        current = self.get_approval(
            approval_id,
            connection=connection,
        )

        if current.status != ApprovalStatus.APPROVED:
            raise ApprovalTransitionConflict(...)

        next_status = (
            ApprovalStatus.EXECUTED
            if action_result.status == ActionStatus.SUCCESS
            else ApprovalStatus.APPROVED
        )

        approval = self.transition_approval_status(
            approval_id,
            expected_statuses={ApprovalStatus.APPROVED},
            next_status=next_status,
            executed_at=utc_now() if next_status == ApprovalStatus.EXECUTED else None,
            connection=connection,
        )

        self.save_action_result(
            approval.run_id,
            action_result,
            connection=connection,
        )

        self.sync_action_todos(
            approval.run_id,
            action_result,
            connection=connection,
        )

        approvals = self.list_approvals(
            run_id=approval.run_id,
            status=None,
            connection=connection,
        )

        self.sync_approval_todos(
            approval.run_id,
            approvals,
            connection=connection,
        )

        self.update_run_status_from_approvals(
            approval.run_id,
            approvals,
            action_result=action_result,
            connection=connection,
        )

        self.reconcile_run_report(
            approval.run_id,
            connection=connection,
        )

        return approval
```

## 3.6 修改 `approval/service.py`

将 `approve_with_action_result()` 改为：

```python
def approve_with_action_result(self, approval_id: str):
    approval = self.store.claim_for_dry_run(approval_id)

    try:
        action_result = self.action_executor.execute(
            approval.action,
            approval.args,
            mode=ActionMode.DRY_RUN,
            approval=approval,
        )
    except Exception:
        self.store.restore_or_mark_failed_claim(approval_id)
        raise

    approval = self.store.finalize_action(
        approval_id=approval_id,
        action_result=action_result,
    )

    tool_result = self._tool_result_from_action_result(
        approval,
        action_result,
    )

    return approval, tool_result, action_result
```

推荐让 `ApprovalStore` 暴露：

```python
claim_for_dry_run()
finalize_action()
reject_pending()
```

不要让 Service 自己拼多次状态更新。

## 3.7 Reject 也要条件更新

当前 reject 应改为：

```text
只有 pending 可以 rejected
```

SQL 必须带：

```sql
WHERE approval_id = ?
  AND status = 'pending'
```

并发 approve/reject 时，只允许一个成功。

## 3.8 API 状态码

修改 `app/api.py`：

- Approval 不存在：`404`
- 非法状态转换：`409 Conflict`
- 参数错误：`400`
- Action 被安全策略阻止：根据现有约定返回 `400` 或 `409`

示例：

```python
except ApprovalTransitionConflict as exc:
    raise HTTPException(
        status_code=409,
        detail=str(exc),
    ) from exc
```

## 3.9 测试

新增真实调用链测试，不要只测 `TraceStore.apply_approval_transition()`。

必须覆盖：

### 并发审批

两个线程同时：

```python
service.approve_with_action_result(approval_id)
```

确认：

- 只有一个成功；
- ActionExecutor 只调用一次；
- 另一个得到 conflict；
- ActionResult 只有一条。

### ActionExecutor 抛异常

确认：

- 不出现 `executed`；
- 不出现 ActionResult；
- 状态明确为 `approved`、`pending` 或专门的 `failed` 状态；
- Todo 和 RunStatus 不伪装成完成。

### Finalize 保存失败

故意让 `save_action_result()` 抛异常。

确认：

- Approval 不变为 executed；
- Todo 不变化；
- RunStatus 不变化；
- Report 不变化。

---

# 四、问题 3：Report 内容和状态分两次提交

## 4.1 当前问题

后台 Report Job 当前执行：

```python
trace_store.save_report_snapshot(report)
trace_store.update_report_status(...)
```

这是两个事务。

可能出现：

```text
final_report 已保存
report_status 仍是 generating
```

或者：

```text
final_report 已保存
report_status 更新失败
Workspace 未刷新
```

## 4.2 修改目标

新增统一方法：

```python
finalize_report_snapshot()
```

一次提交：

```text
Report Steps
Report Todos
final_report
RunStatus
report_status
report_error
report_finished_at
```

## 4.3 修改 `trace/store.py`

新增：

```python
def finalize_report_snapshot(
    self,
    report: ReportSnapshot,
    *,
    report_status: ReportGenerationStatus,
    report_error: str | None = None,
    finished_at=None,
) -> None:
    with self.transaction() as connection:
        for step in report.steps:
            self.save_step(
                step,
                connection=connection,
            )

        for todo in report.todos:
            current = self.get_todo(
                report.run_id,
                todo.todo_id,
                connection=connection,
            )

            if current is not None and should_preserve_todo_state(current):
                continue

            self.save_todo(
                todo,
                connection=connection,
            )

        self.update_run_report(
            report.run_id,
            final_report=report.final_report,
            status=report.run_status,
            connection=connection,
        )

        self.update_report_status(
            report.run_id,
            report_status,
            error=report_error,
            finished_at=finished_at or utc_now(),
            connection=connection,
        )
```

`save_report_snapshot()` 可以保留为兼容方法，但后台 Job 应改用 `finalize_report_snapshot()`。

## 4.4 修改 `app/report_jobs.py`

将：

```python
trace_store.save_report_snapshot(report)

trace_store.update_report_status(
    run_id,
    final_report_status(report),
    finished_at=utc_now(),
)
```

改为：

```python
trace_store.finalize_report_snapshot(
    report,
    report_status=final_report_status(report),
    finished_at=utc_now(),
)
```

## 4.5 Report 失败处理

如果 LLM 本身失败，但 `agent.generate_report()` 返回 deterministic fallback：

```text
report_status = fallback
final_report = deterministic report
```

如果数据库保存、序列化或程序错误导致整个任务失败：

```text
report_status = failed
report_error = 错误信息
```

失败状态更新也要独立可靠。

建议增加：

```python
def mark_report_failed(
    self,
    run_id: str,
    error: str,
) -> None:
    ...
```

## 4.6 测试

新增：

```python
def test_finalize_report_snapshot_rolls_back_content_and_status_together():
    ...
```

故意让 `update_report_status()` 抛异常。

确认：

```text
final_report 没有更新
report_status 没有更新
Report Steps 没有残留
Report Todos 没有部分变化
```

---

# 五、问题 4：Workspace 失败阻止 Report Job 启动

## 5.1 当前问题

当前 API 顺序：

```text
保存 SQLite Snapshot
→ 写 Workspace
→ 提交 Report Job
```

如果 Workspace 写入失败：

```text
SQLite report_status=generating
Report Job 没启动
```

Run 会永久卡住。

## 5.2 修改目标

必须保证：

```text
Workspace 失败不会阻断核心流程
```

SQLite 和后台 Job 优先级高于 Workspace。

## 5.3 推荐顺序

修改 `app/api.py`：

```python
trace_store.save_diagnosis_snapshot(snapshot)

future = submit_report_job(
    agent=agent,
    snapshot=snapshot,
    trace_store=trace_store,
    workspace_writer=workspace_writer,
)

try:
    workspace_writer.write_diagnosis_snapshot(snapshot)
except OSError as exc:
    logger.warning(
        "workspace snapshot write failed",
        extra={
            "run_id": snapshot.run.run_id,
            "error": str(exc),
        },
    )
```

也可以先写 Workspace，但必须放入 try/except，并确保无论失败与否都会提交 Job。

推荐更清晰的结构：

```python
trace_store.save_diagnosis_snapshot(snapshot)

try:
    workspace_writer.write_diagnosis_snapshot(snapshot)
except OSError as exc:
    record_workspace_warning(...)

submit_report_job(...)
```

关键要求：

```text
Workspace 异常不能直接退出 /diagnose
```

## 5.4 Workspace 错误记录

建议在 `diagnosis_runs` 增加：

```text
workspace_error
```

如果不想增加字段，也可以：

- 写日志；
- 在 Run summary 中记录 warning；
- 后续通过 `workspace update_from_trace` 重建。

最低要求：日志中必须包含 `run_id`。

## 5.5 后台 Workspace 更新

`run_report_job()` 中：

```python
workspace_writer.apply_report_snapshot(...)
```

也必须单独容错：

```python
try:
    workspace_writer.apply_report_snapshot(
        report,
        trace_store=trace_store,
    )
except OSError as exc:
    logger.warning(...)
```

Workspace 失败不能把已经成功提交的 Report 改成 failed。

正确语义：

```text
Report 保存成功
Workspace 刷新失败
→ report_status 仍是 ready/fallback
→ 只记录 workspace warning
```

## 5.6 测试

新增：

```python
def test_workspace_failure_does_not_prevent_report_job_submission():
    ...
```

流程：

1. mock `write_diagnosis_snapshot()` 抛 `OSError`；
2. 调用 `/diagnose`；
3. 确认仍返回 202；
4. 确认后台 Report 最终 ready/fallback。

再测试：

```python
def test_workspace_report_update_failure_does_not_mark_report_failed():
    ...
```

---

# 六、问题 5：多 Worker 启动恢复逻辑错误

## 6.1 当前问题

当前每个 FastAPI 进程启动时都会执行：

```python
recover_interrupted_report_jobs()
```

其行为是：

```text
把所有 report_status=generating 的 Run 标成 failed
```

如果使用多个 Uvicorn Worker：

```text
Worker 1 正在生成 Report
Worker 2 启动
→ Worker 2 把 Worker 1 的任务标成 failed
```

## 6.2 当前阶段最低方案

明确只支持单 Worker：

```bash
uvicorn app.api:app   --host 0.0.0.0   --port 18000   --workers 1
```

README、Dockerfile、docker-compose 中都要写清楚。

增加启动检查：

```python
if configured_worker_count > 1:
    logger.warning(
        "in-process report jobs only support a single API worker"
    )
```

## 6.3 更安全的恢复判断

不要仅凭：

```text
report_status=generating
```

就判定任务中断。

增加字段：

```text
report_worker_id
report_heartbeat_at
```

任务开始时：

```text
worker_id = UUID
heartbeat_at = now
```

恢复时只有满足：

```text
report_status=generating
AND heartbeat_at < now - stale_threshold
```

才标记 failed。

当前阶段可先不做心跳，但需要：

1. 限制单 Worker；
2. 将该限制写入 README；
3. 测试启动恢复逻辑。

## 6.4 后续生产方案

未来如果要多 Worker，必须将 Report Job 移出进程：

```text
Celery / RQ / Dramatiq / 自建 SQLite Job Queue
```

但不属于本次修复范围。

---

# 七、建议文件改动清单

## 必改文件

```text
agent/resource_agent.py
approval/service.py
approval/store.py
approval/trace_sync.py
trace/store.py
app/api.py
app/report_jobs.py
tests/test_approval_sqlite_store.py
tests/test_trace_transactions.py
tests/test_api_async_diagnose.py
README.md
```

## 可新增文件

```text
approval/errors.py
tests/test_approval_concurrency.py
tests/test_workspace_failure_tolerance.py
```

---

# 八、推荐实施顺序

## Phase 1：消除 Approval 提前持久化

修改：

```text
approval/service.py
agent/resource_agent.py
trace/store.py
tests/test_trace_transactions.py
```

验收：

```text
collect_and_detect() 不写数据库 Approval
save_diagnosis_snapshot() 原子保存 Approval
```

## Phase 2：实现 Approval 两阶段原子状态机

修改：

```text
approval/store.py
approval/service.py
trace/store.py
approval/trace_sync.py
app/api.py
app/cli.py
```

验收：

```text
pending → approved 只能成功一次
approved → executed 与 ActionResult/Todo/RunStatus 同事务
```

## Phase 3：Report Finalization 原子化

修改：

```text
trace/store.py
app/report_jobs.py
tests/test_trace_transactions.py
tests/test_api_async_diagnose.py
```

验收：

```text
final_report 与 report_status 不会出现一新一旧
```

## Phase 4：Workspace 容错

修改：

```text
app/api.py
app/report_jobs.py
workspace/writer.py
tests/test_workspace_failure_tolerance.py
```

验收：

```text
Workspace 写入失败不影响 SQLite 和 Report Job
```

## Phase 5：单 Worker 约束和文档

修改：

```text
README.md
docker-compose.yml
Dockerfile
```

验收：

```text
所有启动示例都使用 workers=1
README 解释原因
```

---

# 九、必须新增的测试

## 9.1 孤立 Approval 测试

```text
DiagnosisSnapshot 保存失败
→ approvals 表必须为空
```

## 9.2 并发 approve 测试

```text
两个线程同时 approve
→ 一个成功
→ 一个 409/conflict
→ ActionExecutor 只执行一次
```

## 9.3 approve/reject 竞争测试

```text
一个 approve
一个 reject
→ 只能一个成功
```

## 9.4 ActionResult 保存失败测试

```text
Approval 不能进入 executed
Todo 不能 completed
Run 不能 completed
```

## 9.5 Report Finalization 回滚测试

```text
report_status 保存失败
→ final_report、steps、todos 全部回滚
```

## 9.6 Workspace 故障测试

```text
Workspace 写入失败
→ /diagnose 仍返回 202
→ Report Job 仍完成
```

---

# 十、最终验收标准

修复完成后，必须满足：

- `collect_and_detect()` 不直接写 Approval；
- DiagnosisSnapshot 失败不会留下孤立 Approval；
- 同一个 Approval 不会被并发执行两次；
- Approval、ActionResult、Todo、RunStatus 保持一致；
- Report 内容和 report_status 同时提交；
- Workspace 失败不影响核心状态；
- API 明确只支持单 Worker；
- 所有新增测试通过；
- 原有 CLI、API、eval 测试不回归。

---

# 十一、Codex 执行要求

1. 按 Phase 1～5 顺序实施。
2. 每个 Phase 单独提交。
3. 不引入 Celery、Redis 或外部队列。
4. SQLite 始终是唯一状态源。
5. 不在长时间 SQLite 事务中执行真实系统动作。
6. 使用条件 UPDATE 防止并发重复审批。
7. Report Finalization 必须只有一个事务。
8. Workspace 错误只能记录 warning，不能修改成功的 Report 状态。
9. 保持旧 CLI/API 调用兼容。
10. 每个 Phase 完成后执行：

```bash
python -m compileall -q actions app agent approval trace tools scripts eval tests workspace
python -m pytest -q
python eval/run_eval.py
```

建议最后额外运行：

```bash
python -m pytest -q   tests/test_approval_sqlite_store.py   tests/test_trace_transactions.py   tests/test_api_async_diagnose.py
```
