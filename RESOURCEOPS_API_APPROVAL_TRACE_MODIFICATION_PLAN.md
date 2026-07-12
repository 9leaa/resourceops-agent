# ResourceOps Agent 修改方案：API 异步化、Approval SQLite 统一与 Trace 事务化

> 适用仓库：`https://github.com/9leaa/resourceops-agent`
> 本文对应以下三个修改项：
>
> - 修改 2：FastAPI 两阶段异步化
> - 修改 4：Approval 统一使用 SQLite
> - 修改 5：Trace 写入事务化
>
> 推荐实施顺序：**修改 4 → 修改 5 → 修改 2**

---

# 一、总体目标

当前项目存在三个相关问题：

1. FastAPI `/diagnose` 仍然会等待 LLM Report 完成后才返回。
2. Approval 同时保存在 JSONL 和 SQLite，存在状态不一致风险。
3. Trace 保存由多个独立数据库写入组成，中途失败可能留下半条运行记录。

完成本方案后，整体流程应变为：

```text
POST /diagnose
    ↓
工具执行 + Detector
    ↓
SQLite 原子保存 DiagnosisSnapshot
    ↓
立即返回 run_id、findings、approvals
    ↓
用户可以立即 approve / reject
    ↓
后台生成 LLM Report
    ↓
读取最新 SQLite 状态
    ↓
原子保存 ReportSnapshot
```

最终要求：

- Approval 不依赖 LLM Report。
- CLI 和 API 使用同一套审批状态。
- SQLite 是唯一运行时状态源。
- Snapshot、Report、Approval 状态更新具有事务性。
- LLM Report 不得覆盖已经发生的审批或动作状态。

---

# 二、修改 4：Approval 统一使用 SQLite

## 2.1 当前问题

当前 Approval 同时存在于：

```text
var/approvals.jsonl
var/resourceops.sqlite3
```

当前流程大致为：

```text
ApprovalService 更新 JSONL
        ↓
sync_approval_trace()
        ↓
同步到 SQLite
```

风险包括：

- JSONL 已更新，但 SQLite 同步失败。
- SQLite 已更新，但 Workspace 未同步。
- Report 读取 SQLite，API Approval 查询读取 JSONL，看到不同状态。
- LLM Report 完成后，旧的 pending 快照覆盖新状态。

## 2.2 修改目标

以后只使用 SQLite 保存 Approval：

```text
ApprovalService
      ↓
SQLite approvals 表
      ↓
CLI / API / Trace / Workspace / Report
```

`approvals.jsonl` 不再作为运行时状态存储。

可以选择：

1. 删除 JSONL 运行时写入；
2. 保留为手动导出格式；
3. 提供一次性迁移脚本。

## 2.3 需要修改的文件

### `approval/store.py`

将当前 JSONL Store 改为 SQLite Store。

建议保留现有接口，减少调用方修改：

```python
class ApprovalStore:
    def create(self, approval: Approval) -> Approval:
        ...

    def get(self, approval_id: str) -> Approval:
        ...

    def list(self, status: str | None = None) -> list[Approval]:
        ...

    def update_status(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        decided_at=None,
        executed_at=None,
    ) -> Approval:
        ...
```

数据库路径统一复用：

```text
RESOURCEOPS_TRACE_DB
```

不再使用：

```text
RESOURCEOPS_APPROVAL_STORE
```

### Approval 创建规则

创建 Approval 时使用 UPSERT，但不能让旧快照覆盖终态。

推荐行为：

```text
数据库中不存在
→ 插入完整 Approval

数据库中是 pending
→ 允许补充静态字段

数据库中是 executed / rejected / cancelled
→ 禁止被 pending 快照覆盖
```

示例 SQL：

```sql
INSERT INTO approvals (
    approval_id,
    run_id,
    action,
    args_json,
    reason,
    risk,
    status,
    created_at,
    decided_at,
    executed_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(approval_id) DO UPDATE SET
    action = excluded.action,
    args_json = excluded.args_json,
    reason = excluded.reason,
    risk = excluded.risk
WHERE approvals.status = 'pending';
```

不要在普通 Snapshot 保存时覆盖：

```text
executed
rejected
cancelled
```

### `approval/service.py`

让 `ApprovalService` 只使用 SQLite `ApprovalStore`。

审批流程应变为：

```text
读取 pending Approval
→ 校验状态
→ 执行 dry-run
→ 更新 Approval
→ 保存 ActionResult
→ 更新 Todos
→ 更新 RunStatus
→ 更新 Report 动态状态
```

建议把审批状态转换集中到一个方法，不要在 CLI、API 中分别实现。

示例：

```python
def approve_with_action_result(self, approval_id: str):
    ...
```

内部只调用 SQLite Store 和统一状态转换服务。

### `approval/trace_sync.py`

当前文件主要作用是把 ApprovalStore 状态同步到 TraceStore。

统一 SQLite 后，不再需要“复制 Approval 数据”。

建议将其改为：

```text
approval/state_transition.py
```

保留的职责：

- 保存 ActionResult；
- 更新 Approval Todo；
- 更新 Action Todo；
- 更新 RunStatus；
- 重写 Report 动态章节；
- 更新 Workspace。

建议接口：

```python
def apply_approval_transition(
    *,
    trace_store: TraceStore,
    approval: Approval,
    action_result: ActionResult | None = None,
) -> None:
    ...
```

### `trace/store.py`

增加统一的 Approval 数据访问方法：

```python
def create_approval(
    self,
    approval: Approval,
    connection=None,
) -> Approval:
    ...

def get_approval(
    self,
    approval_id: str,
    connection=None,
) -> Approval:
    ...

def list_approvals(
    self,
    status: ApprovalStatus | None = None,
    run_id: str | None = None,
    connection=None,
) -> list[Approval]:
    ...

def update_approval_status(
    self,
    approval_id: str,
    status: ApprovalStatus,
    *,
    decided_at=None,
    executed_at=None,
    connection=None,
) -> Approval:
    ...
```

`ApprovalStore` 可以作为对 `TraceStore` Approval 方法的薄封装，避免重复 SQL。

### `app/api.py`

以下接口保持不变：

```text
GET  /approvals
POST /approvals/{approval_id}/approve
POST /approvals/{approval_id}/reject
POST /approvals/{approval_id}/execute-real
```

但所有数据统一来自 SQLite。

### `app/cli.py`

CLI Approval 查询、approve、reject、execute-real 全部通过 SQLite Store。

删除任何针对 JSONL 状态的特殊同步逻辑。

## 2.4 旧数据迁移

新增：

```text
scripts/migrate_approvals_jsonl_to_sqlite.py
```

流程：

```text
读取 approvals.jsonl
→ 按 approval_id 去重
→ 选择最新状态
→ 写入 SQLite
→ 不覆盖 SQLite 中已有终态
```

建议参数：

```bash
python scripts/migrate_approvals_jsonl_to_sqlite.py   --input var/approvals.jsonl   --db var/resourceops.sqlite3
```

迁移完成后不要自动删除旧文件。

## 2.5 测试

新增：

```text
tests/test_approval_sqlite_store.py
```

至少测试：

1. 创建 Approval 后可从 SQLite 查询。
2. `list(status="pending")` 只返回 pending。
3. approve 后状态变为 executed。
4. reject 后状态变为 rejected。
5. 旧 pending Snapshot 不会覆盖 executed。
6. CLI 和 API 查询到同一状态。
7. 删除 `approvals.jsonl` 后系统仍可运行。
8. 同一个 Approval 重复创建不会产生重复记录。

## 2.6 验收标准

- 项目运行时不再依赖 `approvals.jsonl`。
- SQLite 是 Approval 唯一状态源。
- API、CLI、Trace、Workspace 状态一致。
- Report 不会把 executed/rejected 状态恢复成 pending。
- 旧数据可通过迁移脚本导入。

---

# 三、修改 5：Trace 写入事务化

## 3.1 当前问题

一次诊断需要依次写入：

```text
diagnosis_runs
diagnosis_steps
tool_calls
evidence_items
diagnosis_findings
approvals
diagnosis_todos
```

当前多个保存方法分别打开连接并提交。

如果中途失败，可能出现：

```text
run 已保存
steps 已保存
tool_calls 已保存
findings 保存失败
approvals 和 todos 缺失
```

数据库中会留下不完整 Run。

## 3.2 修改目标

阶段性保存必须满足：

```text
全部成功 → COMMIT
任意失败 → ROLLBACK
```

至少需要保证以下操作原子化：

1. `save_diagnosis_snapshot()`
2. `save_report_snapshot()`
3. Approval 状态转换
4. ActionResult 保存和 Todo 更新

## 3.3 修改 `trace/store.py`

### 增加事务上下文

```python
from contextlib import contextmanager
from collections.abc import Iterator
import sqlite3

@contextmanager
def transaction(self) -> Iterator[sqlite3.Connection]:
    connection = self.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
```

推荐使用：

```sql
BEGIN IMMEDIATE
```

原因：

- Approval 和 Report 可能并发更新；
- 提前获得写锁，减少写到一半才出现锁冲突；
- 当前项目是本地 SQLite，写并发规模较小。

### 保存方法支持复用 Connection

以下方法增加可选参数：

```python
connection: sqlite3.Connection | None = None
```

涉及方法：

```text
save_run
save_step
save_tool_call
save_evidence
save_finding
save_approval
save_todo
save_action_result
update_run_status
update_run_report
reconcile_run_report
```

推荐增加内部辅助方法：

```python
def _execute_with_connection(self, connection, callback):
    if connection is not None:
        return callback(connection)

    with self.transaction() as own_connection:
        return callback(own_connection)
```

也可以拆成：

```text
_public_method()
_internal_method(connection)
```

例如：

```python
def save_run(self, run, connection=None):
    if connection is None:
        with self.transaction() as connection:
            return self._save_run(connection, run)
    return self._save_run(connection, run)
```

## 3.4 重写 `save_diagnosis_snapshot()`

目标：

```python
def save_diagnosis_snapshot(self, snapshot: DiagnosisSnapshot) -> None:
    with self.transaction() as connection:
        self.save_run(snapshot.run, connection=connection)

        for step in snapshot.steps:
            self.save_step(step, connection=connection)

        self.save_tool_results(
            run_id=snapshot.run.run_id,
            steps=snapshot.steps,
            tool_results=snapshot.tool_results,
            connection=connection,
        )

        for evidence in snapshot.evidence_items:
            self.save_evidence(evidence, connection=connection)

        for finding in snapshot.findings:
            self.save_finding(finding, connection=connection)

        for approval_data in snapshot.approvals:
            approval = Approval.model_validate(approval_data)
            self.save_approval(approval, connection=connection)

        for todo in snapshot.todos:
            self.save_todo(todo, connection=connection)
```

整个 DiagnosisSnapshot 必须在一个事务中完成。

## 3.5 重写 `save_report_snapshot()`

目标：

```text
保存 Report Steps
→ 保存 Report Todo
→ 更新 final_report
→ 更新 report_status
→ 更新 run_status
→ COMMIT
```

示例：

```python
def save_report_snapshot(self, report: ReportSnapshot) -> None:
    with self.transaction() as connection:
        for step in report.steps:
            self.save_step(step, connection=connection)

        for todo in report.todos:
            current = self.get_todo(
                report.run_id,
                todo.todo_id,
                connection=connection,
            )
            if current is not None and should_preserve_todo_state(current):
                continue
            self.save_todo(todo, connection=connection)

        self.update_run_report(
            report.run_id,
            final_report=report.final_report,
            status=report.run_status,
            connection=connection,
        )
```

## 3.6 Approval 状态转换事务化

修改 4 完成后，Approval 更新必须在同一事务中执行：

```text
更新 Approval
→ 保存 ActionResult
→ 更新 Approval Todo
→ 更新 Action Todo
→ 更新 RunStatus
→ 重写 final_report 动态章节
→ COMMIT
```

推荐新增：

```python
def apply_approval_transition(
    self,
    *,
    approval: Approval,
    action_result: ActionResult | None,
) -> None:
    with self.transaction() as connection:
        self.save_approval(
            approval,
            connection=connection,
        )

        if action_result is not None:
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
            connection=connection,
        )

        self.reconcile_run_report(
            approval.run_id,
            connection=connection,
        )
```

## 3.7 防止重复写入

当前以下表可能因重复保存产生重复数据：

```text
tool_calls
action_results
```

### `tool_calls`

增加稳定唯一标识：

```text
call_key
```

建议生成方式：

```python
call_key = f"{run_id}:{step_id}:{tool_name}"
```

如果同一工具允许在一个 Plan 中执行多次，则增加：

```text
planned_call_id
```

更可靠的方式：

```python
call_key = f"{run_id}:{planned_call_id}"
```

数据库迁移：

```sql
ALTER TABLE tool_calls ADD COLUMN call_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS
idx_tool_calls_call_key
ON tool_calls(call_key);
```

写入使用：

```sql
INSERT OR REPLACE
```

或：

```sql
ON CONFLICT(call_key) DO UPDATE
```

### `action_results`

为 `ActionResult` 增加：

```text
result_id
```

使用 UUID。

数据库：

```sql
ALTER TABLE action_results ADD COLUMN result_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS
idx_action_results_result_id
ON action_results(result_id);
```

避免同一个结果重复同步时生成多行。

## 3.8 Workspace 写入顺序

SQLite 是状态源，因此写入顺序必须是：

```text
SQLite 事务成功
→ 再更新 Workspace
```

禁止：

```text
先写 Workspace
→ SQLite 保存失败
```

Workspace 写入失败时：

- 不回滚 SQLite；
- 记录 warning；
- 后续可以通过 `workspace update_from_trace` 重建。

## 3.9 测试

新增：

```text
tests/test_trace_transactions.py
```

至少测试：

### 测试 1：DiagnosisSnapshot 正常提交

确认以下表都有对应记录：

```text
diagnosis_runs
diagnosis_steps
tool_calls
evidence_items
diagnosis_findings
approvals
diagnosis_todos
```

### 测试 2：中途异常全部回滚

在保存 finding 或 approval 时故意抛出异常。

确认：

```text
数据库中不存在该 run 的任何记录
```

### 测试 3：Report 保存失败回滚

在 Todo 保存后、Run Report 更新前抛出异常。

确认：

```text
Report Steps 没有残留
Report Todo 没有部分更新
final_report 没有变化
```

### 测试 4：Approval 状态转换失败回滚

模拟：

```text
Approval 已更新
ActionResult 保存失败
```

确认 Approval 仍然是原状态。

### 测试 5：重复保存幂等

同一个 Snapshot 保存两次：

- 不产生重复 tool_calls；
- 不产生重复 action_results；
- Approval 不重复；
- Todo 不重复。

## 3.10 验收标准

- Snapshot 写入是原子的。
- Report 写入是原子的。
- Approval 状态转换是原子的。
- 重复保存不会产生重复记录。
- SQLite 成功后才更新 Workspace。
- 中途异常不会留下半条 Run。

---

# 四、修改 2：FastAPI 两阶段异步化

## 4.1 当前问题

当前 `/diagnose` 流程：

```text
POST /diagnose
→ ResourceAgent.diagnose()
→ collect_and_detect()
→ generate_report()
→ 等 LLM 完成
→ 保存 Trace
→ 返回 HTTP Response
```

如果 LLM Report 需要 96 秒，HTTP 请求就等待 96 秒。

即使 Approval 已经创建，API 用户也无法提前拿到 `approval_id`。

## 4.2 修改目标

改成：

```text
POST /diagnose
→ collect_and_detect()
→ 保存 DiagnosisSnapshot
→ 提交后台 Report Job
→ 立即返回 202
```

后台：

```text
generate_report()
→ 读取最新 Trace
→ reconcile Report
→ 保存 ReportSnapshot
```

用户可以在 Report 生成期间：

```text
GET  /runs/{run_id}
POST /approvals/{approval_id}/approve
POST /approvals/{approval_id}/reject
```

## 4.3 API 设计

## `POST /diagnose`

请求保持不变。

响应状态码：

```text
202 Accepted
```

建议响应：

```json
{
  "run_id": "run_xxx",
  "run_status": "waiting_approval",
  "report_status": "generating",
  "resource_type": "memory",
  "findings": [],
  "approvals": [],
  "requires_approval": true
}
```

这里必须已经完成：

- Resource type 判断；
- ToolPlan；
- 工具执行；
- Detector；
- Evidence；
- Findings；
- Approval 创建；
- SQLite 保存；
- Workspace Snapshot 保存。

这里不等待 LLM Report。

## `GET /runs/{run_id}`

继续返回完整 Trace，但增加：

```json
{
  "run": {
    "status": "waiting_approval",
    "report_status": "generating"
  },
  "approvals": [],
  "final_report": null
}
```

Report 完成后：

```json
{
  "run": {
    "status": "waiting_approval",
    "report_status": "ready"
  },
  "final_report": "..."
}
```

## 可选新增 `GET /runs/{run_id}/report`

生成中：

```json
{
  "run_id": "run_xxx",
  "report_status": "generating",
  "report": null
}
```

完成：

```json
{
  "run_id": "run_xxx",
  "report_status": "ready",
  "source": "llm",
  "latency_ms": 18234,
  "report": "..."
}
```

Fallback：

```json
{
  "run_id": "run_xxx",
  "report_status": "fallback",
  "source": "deterministic",
  "report": "..."
}
```

## 4.4 修改 `app/schemas.py`

新增：

```python
class ReportGenerationStatus(str, Enum):
    NOT_STARTED = "not_started"
    GENERATING = "generating"
    READY = "ready"
    FALLBACK = "fallback"
    FAILED = "failed"
```

在 `DiagnosisRun` 中增加：

```python
report_status: ReportGenerationStatus = ReportGenerationStatus.NOT_STARTED
report_error: str | None = None
```

也可以增加：

```python
report_started_at: datetime | None = None
report_finished_at: datetime | None = None
```

推荐增加，便于观测 Report 延迟。

## 4.5 修改 SQLite Schema

`diagnosis_runs` 增加：

```sql
report_status TEXT NOT NULL DEFAULT 'not_started',
report_error TEXT,
report_started_at TEXT,
report_finished_at TEXT
```

在 `TraceStore.init_db()` 中通过 `_ensure_column()` 兼容旧数据库。

新增方法：

```python
def update_report_status(
    self,
    run_id: str,
    status: ReportGenerationStatus,
    *,
    error: str | None = None,
    started_at=None,
    finished_at=None,
    connection=None,
) -> None:
    ...
```

## 4.6 修改 `app/api.py`

将当前：

```python
result = agent.diagnose(incident)
trace_store.save_agent_result(result)
write_workspace_result(result)
return result.model_dump(mode="json")
```

替换为：

```python
agent = build_resource_agent(...)

snapshot = agent.collect_and_detect(incident)

snapshot.run.report_status = ReportGenerationStatus.GENERATING

trace_store.save_diagnosis_snapshot(snapshot)
WorkspaceWriter().write_diagnosis_snapshot(snapshot)

submit_report_job(
    run_id=snapshot.run.run_id,
    incident=incident,
    planner_mode=request.planner_mode,
    report_mode=request.report_mode,
    agent_mode=request.agent_mode,
)

return JSONResponse(
    status_code=202,
    content=build_async_diagnose_response(snapshot),
)
```

注意：

- 不要直接把含有复杂对象的 Snapshot 放进长期任务注册表；
- 当前本地 MVP 可以把 Snapshot 直接传给线程；
- 后续若要进程重启恢复，应支持从 Trace 重建 Snapshot。

## 4.7 新增 `app/report_jobs.py`

当前阶段不需要直接引入 Celery。

使用进程内线程池：

```python
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock

REPORT_EXECUTOR = ThreadPoolExecutor(max_workers=2)
REPORT_JOBS: dict[str, Future] = {}
REPORT_JOBS_LOCK = Lock()
```

建议接口：

```python
def submit_report_job(
    *,
    agent: ResourceAgent,
    snapshot: DiagnosisSnapshot,
    trace_store: TraceStore,
    workspace_writer: WorkspaceWriter,
) -> Future:
    ...
```

后台任务：

```python
def run_report_job(
    *,
    agent: ResourceAgent,
    snapshot: DiagnosisSnapshot,
    trace_store: TraceStore,
    workspace_writer: WorkspaceWriter,
) -> None:
    run_id = snapshot.run.run_id

    trace_store.update_report_status(
        run_id,
        ReportGenerationStatus.GENERATING,
        started_at=utc_now(),
    )

    try:
        report = agent.generate_report(
            snapshot,
            emit_events=False,
        )

        trace = trace_store.get_trace(run_id)

        report = reconcile_report_snapshot_with_trace(
            report,
            trace,
        )

        trace_store.save_report_snapshot(report)

        final_status = (
            ReportGenerationStatus.READY
            if report.source == "llm" and report.status == "success"
            else ReportGenerationStatus.FALLBACK
        )

        trace_store.update_report_status(
            run_id,
            final_status,
            finished_at=utc_now(),
        )

        workspace_writer.apply_report_snapshot(
            report,
            trace_store=trace_store,
        )

    except Exception as exc:
        trace_store.update_report_status(
            run_id,
            ReportGenerationStatus.FAILED,
            error=str(exc),
            finished_at=utc_now(),
        )
        raise
```

## 4.8 Job Registry

建议提供：

```python
def get_report_job(run_id: str) -> Future | None:
    ...

def cleanup_finished_jobs() -> None:
    ...
```

避免 `REPORT_JOBS` 永久增长。

任务完成时删除：

```python
future.add_done_callback(
    lambda _: remove_job(run_id)
)
```

## 4.9 Report 与 Approval 并发处理

典型情况：

```text
Report Prompt 生成时 approval=pending
用户在 Report 生成期间 approve
LLM 返回内容仍写 pending
```

最终保存前必须：

```text
读取最新 Trace
→ 删除 LLM Report 中的动态审批章节
→ 用最新 Trace 重建审批状态和风险说明
→ 保存 Report
```

现有：

```text
agent/report_reconcile.py
```

可以继续复用。

必须保证调用顺序：

```python
report = agent.generate_report(snapshot)

latest_trace = trace_store.get_trace(run_id)

report = reconcile_report_snapshot_with_trace(
    report,
    latest_trace,
)

trace_store.save_report_snapshot(report)
```

不能直接保存原始 LLM Report。

## 4.10 服务重启处理

进程内线程池存在一个限制：

```text
FastAPI 进程退出
→ 未完成 Report Job 丢失
→ report_status 永久 generating
```

当前本地 MVP 的最低处理方案：

应用启动时扫描：

```sql
SELECT run_id
FROM diagnosis_runs
WHERE report_status = 'generating';
```

统一更新为：

```text
failed
```

并写入：

```text
report_error = "service restarted during report generation"
```

推荐新增启动逻辑：

```python
@app.on_event("startup")
def recover_interrupted_report_jobs():
    ...
```

后续更完整的方案：

```text
从 Trace 重建 DiagnosisSnapshot
→ 重新提交 Report Job
```

但这不属于本次最低修改范围。

## 4.11 并发限制

设置：

```text
max_workers=2
```

原因：

- Report 请求通常是外部网络调用；
- 避免大量请求同时占用上游 LLM；
- 本地 MVP 不需要复杂队列。

建议增加环境变量：

```text
RESOURCEOPS_REPORT_WORKERS=2
```

并限制：

```text
1～4
```

## 4.12 API 测试

新增：

```text
tests/test_api_async_diagnose.py
```

至少测试：

### 测试 1：立即返回

使用一个会阻塞的 Fake LLM。

调用：

```text
POST /diagnose
```

确认：

- HTTP 202；
- 没有等待 LLM 完成；
- 返回 `run_id`；
- 返回 `report_status=generating`。

### 测试 2：Approval 立即可用

LLM 仍阻塞时调用：

```text
GET /approvals
```

确认已经能看到该 Run 的 Approval。

### 测试 3：Report 期间审批

流程：

```text
POST /diagnose
→ approve
→ 释放 Fake LLM
→ 等 Report 完成
```

确认最终报告显示最新 Approval 状态。

### 测试 4：LLM 失败 fallback

Fake LLM 抛异常。

确认：

```text
report_status=fallback
source=deterministic
final_report 非空
```

### 测试 5：后台任务异常

模拟 `save_report_snapshot()` 失败。

确认：

```text
report_status=failed
report_error 非空
```

### 测试 6：重启恢复

创建一条：

```text
report_status=generating
```

执行启动恢复逻辑后确认变为：

```text
failed
```

## 4.13 验收标准

- `/diagnose` 不等待 LLM Report。
- HTTP 返回码为 202。
- 返回时 Approval 已保存并可操作。
- Report 生成期间可以 approve/reject。
- Report 完成后使用最新 Approval 状态。
- LLM 失败时使用 deterministic fallback。
- 后台任务异常有明确 `report_status` 和 `report_error`。
- 服务重启后不会永久停留在 generating。

---

# 五、推荐实施阶段

## Phase 1：Approval SQLite 化

修改：

```text
approval/store.py
approval/service.py
approval/trace_sync.py
trace/store.py
app/api.py
app/cli.py
```

完成后运行：

```bash
python -m pytest -q tests/test_approval_sqlite_store.py
python -m pytest -q
```

## Phase 2：Trace 事务化

修改：

```text
trace/store.py
approval/state_transition.py
tests/test_trace_transactions.py
```

完成后运行：

```bash
python -m pytest -q tests/test_trace_transactions.py
python -m pytest -q
python eval/run_eval.py
```

## Phase 3：FastAPI 两阶段异步化

修改：

```text
app/schemas.py
app/api.py
app/report_jobs.py
trace/store.py
tests/test_api_async_diagnose.py
```

完成后运行：

```bash
python -m pytest -q tests/test_api_async_diagnose.py
python -m pytest -q
python eval/run_eval.py
```

---

# 六、最终验收流程

## 启动 API

```bash
uvicorn app.api:app --host 127.0.0.1 --port 18000
```

## 发起诊断

```bash
curl -i -X POST http://127.0.0.1:18000/diagnose   -H 'content-type: application/json'   -d '{
    "description": "为什么内存快满了？",
    "resource_type": "memory",
    "report_mode": "llm"
  }'
```

预期：

```text
HTTP/1.1 202 Accepted
```

响应中包含：

```text
run_id
report_status=generating
approvals
findings
```

## 在 Report 生成期间审批

```bash
curl -X POST   http://127.0.0.1:18000/approvals/<approval_id>/approve
```

## 查询 Run

```bash
curl http://127.0.0.1:18000/runs/<run_id>
```

预期最终状态：

```text
Approval 状态正确
ActionResult 已保存
Report 状态 ready 或 fallback
Report 动态章节与最新 Trace 一致
Workspace 与 SQLite 一致
```

---

# 七、Codex 执行要求

1. 按 **修改 4 → 修改 5 → 修改 2** 的顺序实施。
2. 每个 Phase 单独提交，不要一次性混合所有改动。
3. 保留现有 CLI 和 API 外部行为，除 `/diagnose` 改为 HTTP 202 外。
4. 不引入 Celery、Redis 或外部队列。
5. SQLite 是唯一运行时状态源。
6. Workspace 永远从 SQLite Trace 更新。
7. Report 不得覆盖最新 Approval 或 Action 状态。
8. 所有新增数据库字段必须兼容旧数据库。
9. 所有状态转换必须有测试。
10. 每个 Phase 完成后执行：

```bash
python -m compileall -q actions app agent approval trace tools scripts eval tests workspace
python -m pytest -q
python eval/run_eval.py
```
