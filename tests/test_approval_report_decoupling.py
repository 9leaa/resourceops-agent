from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.cli import wait_report_after_approval
from agent.report_reconcile import reconcile_report_snapshot_with_trace, report_static_prefix
from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter


class BlockingReportClient:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.approval_id: str | None = None

    def generate_report(self, prompt: str) -> str:
        self.started.set()
        self.release.wait(timeout=5)
        approval_id = self.approval_id or "appr_unknown"
        return f"""## 问题概览
内存诊断报告。

## 关键证据
高内存进程已经由确定性工具发现。

## 诊断发现
存在 memory_process_hogging。

## 建议操作
危险动作必须审批。

## 审批状态
approval_id={approval_id} status=pending 尚未执行。

## 风险说明
危险操作不会自动执行。
"""


class StreamingBlockingReportClient:
    def __init__(self) -> None:
        self.started = Event()
        self.first_chunk_sent = Event()
        self.release = Event()
        self.approval_id: str | None = None

    def generate_report(self, prompt: str) -> str:
        raise AssertionError("stream_report should be used when stream_callback is provided")

    def stream_report(self, prompt: str):
        self.started.set()
        first_chunk = "## 问题概览\n内存诊断报告。\n\n"
        yield first_chunk
        self.first_chunk_sent.set()
        self.release.wait(timeout=5)
        approval_id = self.approval_id or "appr_unknown"
        yield f"""## 关键证据
高内存进程已经由确定性工具发现。

## 诊断发现
存在 memory_process_hogging。

## 建议操作
危险动作必须审批。

## 审批状态
approval_id={approval_id} status=pending 尚未执行。

## 风险说明
危险操作不会自动执行。
"""


class FailingReportClient:
    def generate_report(self, prompt: str) -> str:
        raise RuntimeError("llm report failed")


def build_agent(tmp_path, llm_client):
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)
    agent = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
        report_mode="llm",
        llm_client=llm_client,
    )
    return agent, approval_store, approval_service


def test_approval_is_created_before_llm_report_returns(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, _approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.generate_report, snapshot, emit_events=False)
        assert client.started.wait(timeout=2)

        stored = approval_store.get(approval_id)
        trace = trace_store.get_trace(snapshot.run.run_id)
        assert stored.status == "pending"
        assert trace["approvals"][0]["approval_id"] == approval_id
        assert trace["approvals"][0]["status"] == "pending"
        assert trace["run"]["final_report"] is None

        client.release.set()
        report = future.result(timeout=5)

    assert report.final_report


def test_streamed_report_chunks_are_buffered_before_approval_finishes(tmp_path) -> None:
    client = StreamingBlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)

    chunks: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            agent.generate_report,
            snapshot,
            emit_events=False,
            stream_callback=chunks.append,
        )
        assert client.started.wait(timeout=2)
        assert client.first_chunk_sent.wait(timeout=2)
        assert "".join(chunks).startswith("## 问题概览")

        rejected = approval_service.reject(approval_id)
        sync_approval_trace(trace_store, approval_store, rejected)

        client.release.set()
        report = future.result(timeout=5)

    assert report.final_report
    assert approval_store.get(approval_id).status == "rejected"


def test_reject_can_happen_before_report_finishes_and_is_not_overwritten(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    writer = WorkspaceWriter(tmp_path / "runs")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)
    writer.write_diagnosis_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.generate_report, snapshot, emit_events=False)
        assert client.started.wait(timeout=2)

        rejected = approval_service.reject(approval_id)
        sync_approval_trace(trace_store, approval_store, rejected)

        client.release.set()
        report = future.result(timeout=5)

    trace_store.save_report_snapshot(report)
    writer.apply_report_snapshot(report, trace_store=trace_store)

    trace = trace_store.get_trace(snapshot.run.run_id)
    approval = trace["approvals"][0]
    assert approval["status"] == "rejected"
    assert trace["run"]["status"] == "completed"
    assert trace["run"]["final_report"]

    workspace_approval = (writer.run_dir(snapshot.run.run_id) / "trace" / "approvals.json").read_text()
    assert '"status": "rejected"' in workspace_approval


def test_dry_run_can_happen_before_report_finishes_and_action_state_is_preserved(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.generate_report, snapshot, emit_events=False)
        assert client.started.wait(timeout=2)

        approval, _tool_result, action_result = approval_service.approve_with_action_result(approval_id)
        sync_approval_trace(trace_store, approval_store, approval, action_result)

        client.release.set()
        report = future.result(timeout=5)

    trace_store.save_report_snapshot(report)

    trace = trace_store.get_trace(snapshot.run.run_id)
    assert trace["approvals"][0]["status"] == "executed"
    assert trace["action_results"][0]["mode"] == "dry_run"
    assert trace["action_results"][0]["status"] == "success"
    assert trace["run"]["status"] == "completed"


def test_report_reconcile_replaces_stale_pending_approval_status(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.generate_report, snapshot, emit_events=False)
        assert client.started.wait(timeout=2)

        approval, _tool_result, action_result = approval_service.approve_with_action_result(approval_id)
        sync_approval_trace(trace_store, approval_store, approval, action_result)

        client.release.set()
        report = future.result(timeout=5)

    assert "status=pending 尚未执行" in report.final_report

    reconciled = reconcile_report_snapshot_with_trace(
        report,
        trace_store.get_trace(snapshot.run.run_id),
    )

    assert "status=pending 尚未执行" not in reconciled.final_report
    assert f"approval_id={approval_id}" in reconciled.final_report
    assert "status=executed" in reconciled.final_report
    assert "dry_run=success" in reconciled.final_report


def test_wait_report_after_approval_persists_final_todo_and_report_state(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    writer = WorkspaceWriter(tmp_path / "runs")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)
    writer.write_diagnosis_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.generate_report, snapshot, emit_events=False)
        assert client.started.wait(timeout=2)

        approval, _tool_result, action_result = approval_service.approve_with_action_result(approval_id)
        sync_approval_trace(trace_store, approval_store, approval, action_result)

        client.release.set()
        report = wait_report_after_approval(
            report_future=future,
            run_id=snapshot.run.run_id,
            trace_store=trace_store,
        )

    assert report.run_status == "completed"
    assert [
        todo.status for todo in report.todos
        if todo.approval_id == approval_id
    ][0] == "completed"

    trace_store.save_report_snapshot(report)
    writer.apply_report_snapshot(report, trace_store=trace_store)

    trace = trace_store.get_trace(snapshot.run.run_id)
    report_phase = [
        todo for todo in trace["todos"]
        if todo["level"] == "phase" and todo["display_group"] == "report"
    ][0]
    approval_task = [todo for todo in trace["todos"] if todo.get("approval_id") == approval_id][0]
    workspace_report = (writer.run_dir(snapshot.run.run_id) / "report.md").read_text(encoding="utf-8")

    assert report_phase["status"] == "completed"
    assert approval_task["status"] == "completed"
    assert "status=pending 尚未执行" not in workspace_report
    assert "status=executed" in workspace_report
    assert "dry_run=success" in workspace_report


def test_late_approval_rewrites_existing_report_and_workspace(tmp_path) -> None:
    client = BlockingReportClient()
    agent, approval_store, approval_service = build_agent(tmp_path, client)
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    writer = WorkspaceWriter(tmp_path / "runs")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    client.approval_id = approval_id
    trace_store.save_diagnosis_snapshot(snapshot)
    writer.write_diagnosis_snapshot(snapshot)

    client.release.set()
    report = agent.generate_report(snapshot, emit_events=False)
    trace_store.save_report_snapshot(report)
    writer.apply_report_snapshot(report, trace_store=trace_store)

    run_dir = writer.run_dir(snapshot.run.run_id)
    assert "status=pending 尚未执行" in trace_store.get_trace(snapshot.run.run_id)["run"]["final_report"]
    assert "status=pending 尚未执行" in (run_dir / "report.md").read_text(encoding="utf-8")

    approval, _tool_result, action_result = approval_service.approve_with_action_result(approval_id)
    sync_approval_trace(trace_store, approval_store, approval, action_result)
    writer.update_from_trace(snapshot.run.run_id, trace_store)

    trace = trace_store.get_trace(snapshot.run.run_id)
    workspace_report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "status=pending 尚未执行" not in trace["run"]["final_report"]
    assert "status=executed" in trace["run"]["final_report"]
    assert "dry_run=success" in trace["run"]["final_report"]
    assert "status=pending 尚未执行" not in workspace_report
    assert "status=executed" in workspace_report
    assert "dry_run=success" in workspace_report


def test_report_reconcile_uses_latest_real_action_result() -> None:
    report_text = """## 问题概览
内存诊断报告。

## 审批状态
approval_id=appr_test status=pending 尚未执行。

## 风险说明
在审批仍为 pending 的状态下，不应描述为已执行。
"""
    trace = {
        "approvals": [
            {
                "approval_id": "appr_test",
                "action": "kill_process",
                "risk": "dangerous",
                "status": "executed",
            }
        ],
        "action_results": [
            {
                "approval_id": "appr_test",
                "action": "kill_process",
                "mode": "dry_run",
                "status": "success",
                "preview": "dry-run: kill 7739",
            },
            {
                "approval_id": "appr_test",
                "action": "kill_process",
                "mode": "real",
                "status": "success",
                "preview": "real: terminated pid=7739",
            },
        ],
    }

    from app.schemas import ReportSnapshot

    report = ReportSnapshot(run_id="run_test", final_report=report_text)
    reconciled = reconcile_report_snapshot_with_trace(report, trace)

    assert "status=pending 尚未执行" not in reconciled.final_report
    assert "status=executed" in reconciled.final_report
    assert "real=success preview=real: terminated pid=7739" in reconciled.final_report
    assert "本次已有真实执行结果" in reconciled.final_report


def test_report_static_prefix_hides_dynamic_sections_during_streaming() -> None:
    text = """## 问题概览
内存诊断报告。

## 审批状态
approval_id=appr_test status=pending 尚未执行。
"""

    assert report_static_prefix(text).strip() == "## 问题概览\n内存诊断报告。"


def test_report_failure_does_not_change_pending_approval_status(tmp_path) -> None:
    agent, approval_store, _approval_service = build_agent(tmp_path, FailingReportClient())
    trace_store = TraceStore(tmp_path / "trace.sqlite3")

    snapshot = agent.collect_and_detect(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    approval_id = snapshot.approvals[0]["approval_id"]
    trace_store.save_diagnosis_snapshot(snapshot)

    report = agent.generate_report(snapshot, emit_events=False)
    trace_store.save_report_snapshot(report)

    assert report.status == "fallback"
    assert approval_store.get(approval_id).status == "pending"
    trace = trace_store.get_trace(snapshot.run.run_id)
    assert trace["approvals"][0]["status"] == "pending"
    assert trace["run"]["status"] == "waiting_approval"
