import json
import tarfile

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter, resolve_workspace_root


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class FakeReportClient:
    def generate_report(self, prompt: str) -> str:
        assert "诊断数据" in prompt
        return """## 问题概览
LLM 报告。

## 关键证据
使用已有证据。

## 诊断发现
基于 detector 结果。

## 建议操作
继续观察。

## 审批状态
无待审批危险操作。

## 风险说明
危险操作不会自动执行。
"""


def test_workspace_writer_writes_agent_result(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    run_dir = WorkspaceWriter(tmp_path / "runs").write_agent_result(result)

    assert run_dir == tmp_path / "runs" / result.run.run_id
    assert (run_dir / "metadata.json").exists()
    assert (run_dir / "plan.json").exists()
    assert (run_dir / "todos.json").exists()
    assert (run_dir / "report.md").exists()
    assert (run_dir / "raw" / "tool_outputs.jsonl").exists()
    assert (run_dir / "compact" / "report_context.json").exists()
    assert (run_dir / "trace" / "steps.json").exists()
    assert (run_dir / "trace" / "evidence.json").exists()
    assert (run_dir / "trace" / "findings.json").exists()
    assert (run_dir / "trace" / "approvals.json").exists()
    assert (run_dir / "summary" / "run_summary.json").exists()
    assert (run_dir / "summary" / "run_summary.md").exists()
    assert (run_dir / "compact" / "llm_calls_summary.json").exists()

    metadata = read_json(run_dir / "metadata.json")
    assert metadata["run_id"] == result.run.run_id
    assert metadata["resource_type"] == "memory"
    assert metadata["workspace_version"] == "p14"
    assert "final_report" not in metadata
    assert metadata["report"]["path"] == "report.md"
    assert metadata["compact"]["report_context"] is False
    assert metadata["counts"]["tool_results"] == len(result.tool_results)

    plan = read_json(run_dir / "plan.json")
    assert plan["plan_id"] == result.tool_plan.plan_id
    assert plan["resource_type"] == "memory"

    todos = read_json(run_dir / "todos.json")
    assert any(todo["level"] == "phase" and todo["title"] == "Tool execution" for todo in todos)
    assert any(todo.get("approval_id") == result.approvals[0]["approval_id"] for todo in todos)

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Resource Diagnosis Report" in report

    tool_output_lines = (run_dir / "raw" / "tool_outputs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(tool_output_lines) == len(result.tool_results)
    assert json.loads(tool_output_lines[0])["tool_name"]

    approvals = read_json(run_dir / "trace" / "approvals.json")
    assert approvals[0]["approval_id"] == result.approvals[0]["approval_id"]

    report_context = read_json(run_dir / "compact" / "report_context.json")
    assert report_context["available"] is False
    assert report_context["reason"] == "build_report_context step was not produced"


def test_workspace_writer_writes_report_context_for_llm_report(tmp_path) -> None:
    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        agent_mode="llm_report",
        llm_client=FakeReportClient(),
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    run_dir = WorkspaceWriter(tmp_path / "runs").write_agent_result(result)

    metadata = read_json(run_dir / "metadata.json")
    assert metadata["workspace_version"] == "p14"
    assert metadata["compact"]["report_context"] is True

    report_context = read_json(run_dir / "compact" / "report_context.json")
    assert report_context["available"] is True
    assert report_context["source"] == "diagnosis_step.observation"
    assert report_context["step_action"] == "build_report_context"
    assert report_context["context"]["context_version"] == "p14"
    assert report_context["context"]["incident"]["resource_type"] == "memory"
    assert report_context["context"]["provenance"]["source_tools"]
    assert "tool_context" not in report_context["context"]


def test_workspace_writer_updates_approval_files_from_trace(tmp_path) -> None:
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

    approval, _tool_result = approval_service.approve(approval_id)
    sync_approval_trace(trace_store, approval_store, approval)
    writer.update_from_trace(result.run.run_id, trace_store)

    metadata = read_json(run_dir / "metadata.json")
    approvals = read_json(run_dir / "trace" / "approvals.json")
    todos = read_json(run_dir / "todos.json")
    approval_task = [todo for todo in todos if todo.get("approval_id") == approval_id][0]

    assert metadata["status"] == "completed"
    assert metadata["requires_approval"] is False
    assert approvals[0]["status"] == "executed"
    assert approval_task["status"] == "completed"


def test_workspace_writer_creates_debug_bundle(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    writer = WorkspaceWriter(tmp_path / "runs")
    writer.write_agent_result(result)
    bundle_path = writer.create_bundle(result.run.run_id, bundle_root=tmp_path / "bundles")

    assert bundle_path.exists()
    assert bundle_path.name == f"{result.run.run_id}.tar.gz"

    with tarfile.open(bundle_path, "r:gz") as archive:
        names = archive.getnames()

    assert f"runs/{result.run.run_id}/metadata.json" in names
    assert f"runs/{result.run.run_id}/report.md" in names
    assert f"runs/{result.run.run_id}/trace/approvals.json" in names
    assert all(".env" not in name for name in names)
    assert all("approvals.jsonl" not in name for name in names)


def test_resolve_workspace_root_uses_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(tmp_path / "custom-runs"))
    assert resolve_workspace_root() == tmp_path / "custom-runs"
