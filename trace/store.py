from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.schemas import Approval, DiagnosisFinding, DiagnosisRun, DiagnosisStep, EvidenceItem, utc_now
from tools.registry import ToolExecutionResult

if TYPE_CHECKING:
    from agent.resource_agent import ResourceAgentResult


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
                """
            )

    def save_agent_result(self, result: ResourceAgentResult) -> None:
        self.save_run(result.run)
        for step in result.steps:
            self.save_step(step)
        for index, tool_result in enumerate(result.tool_results):
            step_id = result.steps[index].step_id if index < len(result.steps) else None
            self.save_tool_call(result.run.run_id, step_id, tool_result)
        for evidence in result.evidence_items:
            self.save_evidence(evidence)
        for finding in result.findings:
            self.save_finding(finding)
        for approval_data in result.approvals:
            self.save_approval(Approval.model_validate(approval_data))

    def save_run(self, run: DiagnosisRun) -> None:
        payload = run.model_dump(mode="json")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO diagnosis_runs (
                    run_id, incident_id, status, user_input, resource_type, agent_mode,
                    final_report, root_cause, summary, started_at, ended_at, error
                )
                VALUES (
                    :run_id, :incident_id, :status, :user_input, :resource_type, :agent_mode,
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

        return {
            "run": dict(run),
            "steps": [self._step_to_dict(row) for row in steps],
            "tool_calls": [self._tool_call_to_dict(row) for row in tool_calls],
            "evidence_items": [self._evidence_to_dict(row) for row in evidence],
            "findings": [self._finding_to_dict(row) for row in findings],
            "approvals": [self._approval_to_dict(row) for row in approvals],
        }

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
