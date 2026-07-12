from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from actions.executor import ActionMode, ActionResult, ActionStatus
from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import Approval, ApprovalStatus, DiagnosisStep, ReportSnapshot, ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


def table_count(db_path: Path, table: str, run_id: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )


def build_memory_result(trace_store: TraceStore | None = None):
    approval_service = None
    if trace_store is not None:
        approval_service = ApprovalService(store=ApprovalStore(trace_store=trace_store))
    return ResourceAgent(registry=MemoryPressureRegistry(), approval_service=approval_service).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )


def test_save_agent_result_is_transactional_on_failure(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite3"
    trace_store = TraceStore(db_path)
    result = build_memory_result()

    def fail_save_finding(_finding, *, connection=None):
        raise RuntimeError("forced finding failure")

    monkeypatch.setattr(trace_store, "save_finding", fail_save_finding)

    with pytest.raises(RuntimeError, match="forced finding failure"):
        trace_store.save_agent_result(result)

    with pytest.raises(KeyError):
        trace_store.get_trace(result.run.run_id)
    assert table_count(db_path, "diagnosis_steps", result.run.run_id) == 0
    assert table_count(db_path, "tool_calls", result.run.run_id) == 0


def test_save_report_snapshot_is_transactional_on_failure(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite3"
    trace_store = TraceStore(db_path)
    result = build_memory_result()
    trace_store.save_agent_result(result)

    report_step = DiagnosisStep(
        run_id=result.run.run_id,
        step_index=999,
        thought="forced report step",
        action="llm_report",
        observation={"status": "success"},
        observation_preview="forced report",
    )
    report = ReportSnapshot(
        run_id=result.run.run_id,
        final_report="new report should rollback",
        steps=[report_step],
        todos=result.todos,
    )

    def fail_update_report(*_args, **_kwargs):
        raise RuntimeError("forced report failure")

    monkeypatch.setattr(trace_store, "update_run_report", fail_update_report)

    original_trace = trace_store.get_trace(result.run.run_id)
    with pytest.raises(RuntimeError, match="forced report failure"):
        trace_store.save_report_snapshot(report)

    trace = trace_store.get_trace(result.run.run_id)
    assert trace["run"]["final_report"] == original_trace["run"]["final_report"]
    assert all(step["step_id"] != report_step.step_id for step in trace["steps"])


def test_approval_transition_rolls_back_on_action_result_failure(monkeypatch, tmp_path: Path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    result = build_memory_result(trace_store)
    trace_store.save_agent_result(result)

    pending = Approval.model_validate(result.approvals[0])
    executed = pending.model_copy(update={"status": ApprovalStatus.EXECUTED})
    action_result = ActionResult(
        action=executed.action,
        args=executed.args,
        mode=ActionMode.DRY_RUN,
        status=ActionStatus.SUCCESS,
        approval_id=executed.approval_id,
        preview="dry-run",
    )

    def fail_save_action_result(*_args, **_kwargs):
        raise RuntimeError("forced action result failure")

    monkeypatch.setattr(trace_store, "save_action_result", fail_save_action_result)

    with pytest.raises(RuntimeError, match="forced action result failure"):
        trace_store.apply_approval_transition(approval=executed, action_result=action_result)

    trace = trace_store.get_trace(result.run.run_id)
    assert trace["approvals"][0]["status"] == "pending"
    assert trace["action_results"] == []


def test_repeated_saves_are_idempotent_for_tool_and_action_results(tmp_path: Path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    result = build_memory_result(trace_store)
    trace_store.save_agent_result(result)
    trace_store.save_agent_result(result)

    trace = trace_store.get_trace(result.run.run_id)
    assert len(trace["tool_calls"]) == len(result.tool_results)

    approval = Approval.model_validate(result.approvals[0]).model_copy(update={"status": ApprovalStatus.EXECUTED})
    action_result = ActionResult(
        action=approval.action,
        args=approval.args,
        mode=ActionMode.DRY_RUN,
        status=ActionStatus.SUCCESS,
        approval_id=approval.approval_id,
        preview="dry-run",
    )

    trace_store.apply_approval_transition(approval=approval, action_result=action_result)
    trace_store.apply_approval_transition(approval=approval, action_result=action_result)

    trace = trace_store.get_trace(result.run.run_id)
    assert len(trace["action_results"]) == 1
    action_todos = [todo for todo in trace["todos"] if todo.get("source") == "action_executor"]
    assert len(action_todos) == 1
