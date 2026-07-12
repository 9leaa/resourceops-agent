from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.schemas import (
    Approval,
    ApprovalStatus,
    DiagnosisFinding,
    DiagnosisRun,
    DiagnosisStep,
    DiagnosisTodo,
    EvidenceItem,
    RunStatus,
    TodoDisplayGroup,
    TodoLevel,
    TodoStatus,
    utc_now,
)
from tools.registry import ToolExecutionResult

if TYPE_CHECKING:
    from app.schemas import DiagnosisSnapshot, ReportSnapshot, ResourceAgentResult


DEFAULT_TRACE_DB = Path(__file__).resolve().parents[1] / "var" / "resourceops.sqlite3"


def resolve_trace_db(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("RESOURCEOPS_TRACE_DB", DEFAULT_TRACE_DB))


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def schema_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw) if raw is not None else ""


def should_preserve_todo_state(todo: DiagnosisTodo) -> bool:
    group = schema_value(todo.display_group)
    status = schema_value(todo.status)
    if group not in {TodoDisplayGroup.APPROVAL.value, TodoDisplayGroup.ACTIONS.value}:
        return False
    return status in {
        TodoStatus.COMPLETED.value,
        TodoStatus.FAILED.value,
        TodoStatus.SKIPPED.value,
    }


class TraceStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = resolve_trace_db(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_runs (
                    run_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    agent_mode TEXT NOT NULL,
                    planner_mode TEXT NOT NULL DEFAULT 'deterministic',
                    report_mode TEXT NOT NULL DEFAULT 'template',
                    final_report TEXT,
                    root_cause TEXT,
                    summary TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS diagnosis_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    thought TEXT NOT NULL,
                    action TEXT,
                    args_json TEXT NOT NULL,
                    observation_json TEXT,
                    observation_preview TEXT,
                    latency_ms INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_id TEXT,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    result_json TEXT,
                    preview TEXT,
                    summary TEXT,
                    permission_level TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id),
                    FOREIGN KEY(step_id) REFERENCES diagnosis_steps(step_id)
                );

                CREATE TABLE IF NOT EXISTS evidence_items (
                    evidence_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_tool TEXT NOT NULL,
                    category TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS diagnosis_findings (
                    finding_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    finding_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    recommended_actions_json TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    executed_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS diagnosis_todos (
                    todo_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    todo_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'task',
                    parent_todo_id TEXT,
                    display_group TEXT NOT NULL DEFAULT 'tools',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    tool_name TEXT,
                    args_json TEXT NOT NULL,
                    planned_call_id TEXT,
                    approval_id TEXT,
                    depends_on_json TEXT NOT NULL,
                    assigned_agent TEXT,
                    result_preview TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS action_results (
                    action_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    approval_id TEXT,
                    action TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    pre_check_json TEXT NOT NULL,
                    execution_json TEXT NOT NULL,
                    post_check_json TEXT NOT NULL,
                    preview TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES diagnosis_runs(run_id)
                );

                """
            )
            self._ensure_column(
                connection,
                "diagnosis_runs",
                "planner_mode",
                "TEXT NOT NULL DEFAULT 'deterministic'",
            )
            self._ensure_column(
                connection,
                "diagnosis_runs",
                "report_mode",
                "TEXT NOT NULL DEFAULT 'template'",
            )
            self._ensure_column(
                connection,
                "diagnosis_todos",
                "level",
                "TEXT NOT NULL DEFAULT 'task'",
            )
            self._ensure_column(
                connection,
                "diagnosis_todos",
                "parent_todo_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "diagnosis_todos",
                "display_group",
                "TEXT NOT NULL DEFAULT 'tools'",
            )
            self._ensure_column(
                connection,
                "diagnosis_todos",
                "sort_order",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "diagnosis_todos",
                "approval_id",
                "TEXT"
            )
    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_agent_result(self, result: ResourceAgentResult) -> None:
        self.save_run(result.run)
        for step in result.steps:
            self.save_step(step)
        used_step_ids: set[str] = set()
        for tool_result in result.tool_results:
            step_id = self._match_tool_step_id(result.steps, tool_result.tool_name, used_step_ids)
            if step_id is not None:
                used_step_ids.add(step_id)
            self.save_tool_call(result.run.run_id, step_id, tool_result)
        for evidence in result.evidence_items:
            self.save_evidence(evidence)
        for finding in result.findings:
            self.save_finding(finding)
        for approval_data in result.approvals:
            self.save_approval(Approval.model_validate(approval_data))
        for todo in result.todos:
            self.save_todo(todo)

    def save_diagnosis_snapshot(self, snapshot: DiagnosisSnapshot) -> None:
        self.save_run(snapshot.run)
        for step in snapshot.steps:
            self.save_step(step)
        used_step_ids: set[str] = set()
        for tool_result in snapshot.tool_results:
            step_id = self._match_tool_step_id(snapshot.steps, tool_result.tool_name, used_step_ids)
            if step_id is not None:
                used_step_ids.add(step_id)
            self.save_tool_call(snapshot.run.run_id, step_id, tool_result)
        for evidence in snapshot.evidence_items:
            self.save_evidence(evidence)
        for finding in snapshot.findings:
            self.save_finding(finding)
        for approval_data in snapshot.approvals:
            self.save_approval(Approval.model_validate(approval_data))
        for todo in snapshot.todos:
            self.save_todo(todo)

    def save_report_snapshot(self, report: ReportSnapshot) -> None:
        for step in report.steps:
            self.save_step(step)
        for todo in report.todos:
            current = self.get_todo(report.run_id, todo.todo_id)
            if current is not None and should_preserve_todo_state(current):
                continue
            self.save_todo(todo)
        self.update_run_report(
            report.run_id,
            final_report=report.final_report,
            status=report.run_status,
        )
        

    @staticmethod
    def _match_tool_step_id(
        steps: list[DiagnosisStep],
        tool_name: str,
        used_step_ids: set[str],
    ) -> str | None:
        for step in steps:
            if step.step_id in used_step_ids:
                continue
            if step.action == tool_name:
                return step.step_id
        return None

    @staticmethod
    def _todo_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["args"] = loads(data.pop("args_json"))
        data["depends_on"] = loads(data.pop("depends_on_json"))
        return data
    
    def save_run(self, run: DiagnosisRun) -> None:
        payload = run.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO diagnosis_runs (
                    run_id, incident_id, status, user_input, resource_type, agent_mode, planner_mode, report_mode,
                    final_report, root_cause, summary, started_at, ended_at, error      
                )
                VALUES (
                    :run_id, :incident_id, :status, :user_input, :resource_type, :agent_mode, :planner_mode, :report_mode,
                    :final_report, :root_cause, :summary, :started_at, :ended_at, :error
                )
                """,
                payload,
            )

    def save_step(self, step: DiagnosisStep) -> None:
        payload = step.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO diagnosis_steps (
                    step_id, run_id, step_index, thought, action, args_json,
                    observation_json, observation_preview, latency_ms, status, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["step_id"],
                    payload["run_id"],
                    payload["step_index"],
                    payload["thought"],
                    payload["action"],
                    dumps(payload["args"]),
                    dumps(payload["observation"]),
                    payload["observation_preview"],
                    payload["latency_ms"],
                    payload["status"],
                    payload["error"],
                    payload["created_at"],
                ),
            )

    def save_tool_call(self, run_id: str, step_id: str | None, result: ToolExecutionResult) -> None:
        payload = result.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (
                    run_id, step_id, tool_name, args_json, result_json, preview, summary,
                    permission_level, latency_ms, status, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    step_id,
                    payload["tool_name"],
                    dumps(payload["validated_args"]),
                    dumps(payload["data"]),
                    payload["preview"],
                    payload["summary"],
                    payload["permission_level"],
                    payload["latency_ms"],
                    payload["status"],
                    payload["error"],
                    utc_now().isoformat(),
                ),
            )

    def save_evidence(self, evidence: EvidenceItem) -> None:
        payload = evidence.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO evidence_items (
                    evidence_id, run_id, source_tool, category, level, message,
                    data_json, confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["evidence_id"],
                    payload["run_id"],
                    payload["source_tool"],
                    payload["category"],
                    payload["level"],
                    payload["message"],
                    dumps(payload["data"]),
                    payload["confidence"],
                    payload["created_at"],
                ),
            )

    def save_finding(self, finding: DiagnosisFinding) -> None:
        payload = finding.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO diagnosis_findings (
                    finding_id, run_id, finding_type, title, description,
                    evidence_ids_json, confidence, recommended_actions_json, requires_approval
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["finding_id"],
                    payload["run_id"],
                    payload["finding_type"],
                    payload["title"],
                    payload["description"],
                    dumps(payload["evidence_ids"]),
                    payload["confidence"],
                    dumps(payload["recommended_actions"]),
                    int(payload["requires_approval"]),
                ),
            )

    def save_approval(self, approval: Approval) -> None:
        payload = approval.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO approvals (
                    approval_id, run_id, action, args_json, reason, risk,
                    status, created_at, decided_at, executed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["approval_id"],
                    payload["run_id"],
                    payload["action"],
                    dumps(payload["args"]),
                    payload["reason"],
                    payload["risk"],
                    payload["status"],
                    payload["created_at"],
                    payload["decided_at"],
                    payload["executed_at"],
                ),
            )

    def save_todo(self, todo: DiagnosisTodo) -> None:
        payload = todo.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO diagnosis_todos (
                    todo_id, run_id, todo_index, title, status, level, parent_todo_id,
                    display_group, sort_order, source, tool_name, args_json,
                    planned_call_id, approval_id, depends_on_json, assigned_agent, result_preview,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["todo_id"],
                    payload["run_id"],
                    payload["todo_index"],
                    payload["title"],
                    payload["status"],
                    payload["level"],
                    payload["parent_todo_id"],
                    payload["display_group"],
                    payload["sort_order"],
                    payload["source"],
                    payload["tool_name"],
                    dumps(payload["args"]),
                    payload["planned_call_id"],
                    payload["approval_id"],
                    dumps(payload["depends_on"]),
                    payload["assigned_agent"],
                    payload["result_preview"],
                    payload["error"],
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )

    def get_todo(self, run_id: str, todo_id: str) -> DiagnosisTodo | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnosis_todos WHERE run_id = ? AND todo_id = ?",
                (run_id, todo_id),
            ).fetchone()
        return DiagnosisTodo.model_validate(self._todo_to_dict(row)) if row is not None else None
    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, incident_id, status, user_input, resource_type,
                       summary, started_at, ended_at
                FROM diagnosis_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run_status(self, run_id: str, status: RunStatus, ended_at: str | None = None) -> None:
        ended_at = ended_at or utc_now().isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE diagnosis_runs
                SET status = ?, ended_at = ?
                WHERE run_id = ?
                """,
                (status.value, ended_at, run_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"run not found: {run_id}")

    def update_run_report(
        self,
        run_id: str,
        *,
        final_report: str,
        status: RunStatus,
        ended_at: str | None = None,
    ) -> None:
        ended_at = ended_at or utc_now().isoformat()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT status FROM diagnosis_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"run not found: {run_id}")

            current_status = str(current["status"])
            next_status = current_status if current_status in {"completed", "failed"} else schema_value(status)
            connection.execute(
                """
                UPDATE diagnosis_runs
                SET final_report = ?, status = ?, ended_at = ?
                WHERE run_id = ?
                """,
                (final_report, next_status, ended_at, run_id),
            )

    def reconcile_run_report(self, run_id: str) -> None:
        """Refresh final_report dynamic sections from the latest trace state."""

        trace = self.get_trace(run_id)
        final_report = trace.get("run", {}).get("final_report")
        if not final_report:
            return

        from agent.report_reconcile import reconcile_report_text_with_trace

        reconciled = reconcile_report_text_with_trace(str(final_report), trace)
        if reconciled == final_report:
            return

        with self.connect() as connection:
            connection.execute(
                """
                UPDATE diagnosis_runs
                SET final_report = ?
                WHERE run_id = ?
                """,
                (reconciled, run_id),
            )

    def get_trace(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            run = connection.execute("SELECT * FROM diagnosis_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            steps = connection.execute(
                "SELECT * FROM diagnosis_steps WHERE run_id = ? ORDER BY step_index",
                (run_id,),
            ).fetchall()
            tool_calls = connection.execute(
                "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY call_id",
                (run_id,),
            ).fetchall()
            evidence = connection.execute(
                "SELECT * FROM evidence_items WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
            findings = connection.execute(
                "SELECT * FROM diagnosis_findings WHERE run_id = ? ORDER BY finding_id",
                (run_id,),
            ).fetchall()
            approvals = connection.execute(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
            todos = connection.execute(
                """
                SELECT * FROM diagnosis_todos
                WHERE run_id = ?
                ORDER BY level, sort_order, todo_index
                """,
                (run_id,),
            ).fetchall()
            action_results = connection.execute(
                "SELECT * FROM action_results WHERE run_id = ? ORDER BY action_result_id",
                (run_id,),
            ).fetchall()

        return {
            "run": dict(run),
            "steps": [self._step_to_dict(row) for row in steps],
            "tool_calls": [self._tool_call_to_dict(row) for row in tool_calls],
            "evidence_items": [self._evidence_to_dict(row) for row in evidence],
            "findings": [self._finding_to_dict(row) for row in findings],
            "approvals": [self._approval_to_dict(row) for row in approvals],
            "todos": [self._todo_to_dict(row) for row in todos],
            "action_results": [self._action_result_to_dict(row) for row in action_results],
        }
    def list_todos(self, run_id: str) -> list[DiagnosisTodo]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM diagnosis_todos
                WHERE run_id = ?
                ORDER BY level, sort_order, todo_index
                """,
                (run_id,),
            ).fetchall()
        return [DiagnosisTodo.model_validate(self._todo_to_dict(row)) for row in rows]
    def sync_approval_todos(self, run_id: str, approvals: list[Approval]) -> None:
        todos = self.list_todos(run_id)
        if not todos:
            return

        approvals_by_id = {approval.approval_id: approval for approval in approvals}
        pending_count = sum(1 for approval in approvals if approval.status == ApprovalStatus.PENDING)

        for todo in todos:
            if todo.level == TodoLevel.PHASE and todo.display_group == TodoDisplayGroup.APPROVAL:
                if pending_count:
                    todo = todo.model_copy(
                        update={
                            "status": TodoStatus.WAITING_APPROVAL,
                            "result_preview": f"{pending_count} approval(s) pending",
                            "updated_at": utc_now(),
                        }
                    )
                elif approvals:
                    todo = todo.model_copy(
                        update={
                            "status": TodoStatus.COMPLETED,
                            "result_preview": "all approvals resolved",
                            "updated_at": utc_now(),
                        }
                    )
                else:
                    todo = todo.model_copy(
                        update={
                            "status": TodoStatus.COMPLETED,
                            "result_preview": "no approvals",
                            "updated_at": utc_now(),
                        }
                    )
                self.save_todo(todo)
                continue

            if todo.source != "approval" or not todo.approval_id:
                continue

            approval = approvals_by_id.get(todo.approval_id)
            if approval is None:
                continue

            if approval.status == ApprovalStatus.PENDING:
                todo = todo.model_copy(update={"status": TodoStatus.WAITING_APPROVAL, "updated_at": utc_now()})
            elif approval.status == ApprovalStatus.EXECUTED:
                todo = todo.model_copy(
                    update={
                        "status": TodoStatus.COMPLETED,
                        "result_preview": f"executed: {approval.action}",
                        "error": None,
                        "updated_at": utc_now(),
                    }
                )
            elif approval.status == ApprovalStatus.REJECTED:
                todo = todo.model_copy(
                    update={
                        "status": TodoStatus.SKIPPED,
                        "result_preview": f"rejected: {approval.action}",
                        "updated_at": utc_now(),
                    }
                )
            else:
                todo = todo.model_copy(
                    update={
                        "status": TodoStatus.SKIPPED,
                        "result_preview": f"{approval.status}: {approval.action}",
                        "updated_at": utc_now(),
                    }
                )

            self.save_todo(todo)
    
    def save_action_result(self, run_id: str, result: Any) -> None:
        """保存 P12 ActionExecutor 产生的 dry-run 结果。"""

        payload = result.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO action_results (
                    run_id, approval_id, action, mode, status, args_json,
                    pre_check_json, execution_json, post_check_json,
                    preview, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload["approval_id"],
                    payload["action"],
                    payload["mode"],
                    payload["status"],
                    dumps(payload["args"]),
                    dumps(payload["pre_check"]),
                    dumps(payload["execution"]),
                    dumps(payload["post_check"]),
                    payload["preview"],
                    payload["error"],
                    payload["created_at"],
                ),
            )

    def sync_action_todos(self, run_id: str, action_result: Any) -> None:
        """根据 ActionResult 更新 Action execution 阶段和小任务。

        Approval task 表示“人是否批准”；Action task 表示“批准后的动作 dry-run
        是否完成”。这两个状态需要分开，避免把审批完成误解成动作完成。
        """

        todos = self.list_todos(run_id)
        if not todos:
            return

        action_phase = next(
            (
                todo for todo in todos
                if str(todo.level) == TodoLevel.PHASE.value
                and str(todo.display_group) == TodoDisplayGroup.ACTIONS.value
            ),
            None,
        )
        if action_phase is None:
            return

        success = str(action_result.status) == "success"
        phase_status = TodoStatus.COMPLETED if success else TodoStatus.FAILED
        action_status = TodoStatus.COMPLETED if success else TodoStatus.FAILED

        action_phase = action_phase.model_copy(
            update={
                "status": phase_status,
                "result_preview": action_result.preview,
                "error": action_result.error,
                "updated_at": utc_now(),
                }
        )
        self.save_todo(action_phase)

        existing = next(
            (
                todo for todo in todos
                if todo.source == "action_executor"
                and todo.approval_id == action_result.approval_id
                and todo.tool_name == action_result.action
            ),
            None,
        )

        if existing is None:
            # 诊断阶段只预留 Action execution phase；真正的 action task 在
            # approve 后根据 ActionResult 创建。
            max_index = max((todo.todo_index for todo in todos), default=0)
            max_action_order = max(
                (
                    todo.sort_order for todo in todos
                    if str(todo.display_group) == TodoDisplayGroup.ACTIONS.value
                ),
                default=0,
            )
            existing = DiagnosisTodo(
                run_id=run_id,
                todo_index=max_index + 1,
                sort_order=max_action_order + 1,
                title=f"Action: {action_result.action}",
                status=action_status,
                level=TodoLevel.TASK,
                parent_todo_id=action_phase.todo_id,
                display_group=TodoDisplayGroup.ACTIONS,
                source="action_executor",
                tool_name=action_result.action,
                args=action_result.args,
                approval_id=action_result.approval_id,
                assigned_agent="action_executor",
                result_preview=action_result.preview,
                error=action_result.error,
            )
        else:
            existing = existing.model_copy(
                update={
                    "status": action_status,
                    "result_preview": action_result.preview,
                    "error": action_result.error,
                    "updated_at": utc_now(),
                }
            )

        self.save_todo(existing)





    @staticmethod
    def _step_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["args"] = loads(data.pop("args_json"))
        data["observation"] = loads(data.pop("observation_json"))
        return data

    @staticmethod
    def _tool_call_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["args"] = loads(data.pop("args_json"))
        data["result"] = loads(data.pop("result_json"))
        return data

    @staticmethod
    def _evidence_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["data"] = loads(data.pop("data_json"))
        return data

    @staticmethod
    def _finding_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["evidence_ids"] = loads(data.pop("evidence_ids_json"))
        data["recommended_actions"] = loads(data.pop("recommended_actions_json"))
        data["requires_approval"] = bool(data["requires_approval"])
        return data

    @staticmethod
    def _approval_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["args"] = loads(data.pop("args_json"))
        return data

    @staticmethod
    def _action_result_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["args"] = loads(data.pop("args_json"))
        data["pre_check"] = loads(data.pop("pre_check_json"))
        data["execution"] = loads(data.pop("execution_json"))
        data["post_check"] = loads(data.pop("post_check_json"))
        return data
