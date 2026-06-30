import json

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter


def test_p12_approve_records_action_result_in_trace_and_workspace(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    trace_store.save_agent_result(result)

    writer = WorkspaceWriter(tmp_path / "runs")
    run_dir = writer.write_agent_result(result)

    approval_id = result.approvals[0]["approval_id"]
    approval, tool_result, action_result = approval_service.approve_with_action_result(approval_id)
    sync_approval_trace(trace_store, approval_store, approval, action_result)
    writer.update_from_trace(result.run.run_id, trace_store)

    trace = trace_store.get_trace(result.run.run_id)
    action_task = [
        todo for todo in trace["todos"]
        if todo.get("source") == "action_executor"
    ][0]

    assert tool_result.status == "success"
    assert action_result.mode == "dry_run"
    assert action_result.status == "success"
    assert action_result.execution["changed_system_state"] is False

    assert trace["action_results"][0]["action"] == "kill_process"
    assert trace["action_results"][0]["mode"] == "dry_run"
    assert trace["action_results"][0]["status"] == "success"
    assert action_task["status"] == "completed"

    workspace_actions = json.loads(
        (run_dir / "trace" / "action_results.json").read_text(encoding="utf-8")
    )
    workspace_todos = json.loads(
        (run_dir / "todos.json").read_text(encoding="utf-8")
    )

    workspace_action_task = [
        todo for todo in workspace_todos
        if todo.get("source") == "action_executor"
    ][0]

    assert workspace_actions[0]["mode"] == "dry_run"
    assert workspace_actions[0]["status"] == "success"
    assert workspace_action_task["status"] == "completed"