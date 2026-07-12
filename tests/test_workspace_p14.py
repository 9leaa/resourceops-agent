import json

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_workspace_steps_are_compact_and_reference_raw_artifacts(tmp_path) -> None:
    result = ResourceAgent(registry=MemoryPressureRegistry()).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    run_dir = WorkspaceWriter(tmp_path / "runs").write_agent_result(result)

    steps = read_json(run_dir / "trace" / "steps.json")
    serialized = json.dumps(steps, ensure_ascii=False)
    tool_steps = [step for step in steps if step.get("artifact_ref", "").startswith("../raw/tool_outputs")]

    assert all("tool_catalog" not in step.get("observation", {}) for step in steps)
    assert all("tool_plan" not in step.get("observation", {}) for step in steps)
    assert all("processes" not in step.get("observation", {}) for step in steps)
    assert tool_steps
    assert all("observation_preview" in step for step in steps)


def test_action_refresh_creates_remediation_summary_and_reconciles_report(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)
    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))
    trace_store.save_agent_result(result)
    writer = WorkspaceWriter(tmp_path / "runs")
    run_dir = writer.write_agent_result(result)
    original_report = (run_dir / "report.md").read_text(encoding="utf-8")

    approval, _tool_result, action_result = approval_service.approve_with_action_result(
        result.approvals[0]["approval_id"]
    )
    sync_approval_trace(trace_store, approval_store, approval, action_result)
    writer.update_from_trace(result.run.run_id, trace_store)

    remediation = (run_dir / "remediation_summary.md").read_text(encoding="utf-8")
    refreshed_report = (run_dir / "report.md").read_text(encoding="utf-8")
    summary = read_json(run_dir / "summary" / "run_summary.json")
    assert "Mode: dry_run" in remediation
    assert "Changed system state: False" in remediation
    assert refreshed_report != original_report
    assert "status=executed" in refreshed_report
    assert "dry_run=success" in refreshed_report
    assert summary["remediation_summary_available"] is True
    assert "report_generated_before_action_execution" in summary["warnings"]
