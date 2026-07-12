from __future__ import annotations

import re
import time
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from agent.resource_agent import ResourceAgent
from app import api
from app.api import app
from app.report_jobs import recover_interrupted_report_jobs, run_report_job
from app.schemas import ReportGenerationStatus, ResourceIncident
from approval.service import ApprovalService
from approval.store import ApprovalStore
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter


class BlockingReportClient:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def generate_report(self, prompt: str) -> str:
        self.started.set()
        self.release.wait(timeout=5)
        approval_id = approval_id_from_prompt(prompt)
        return f"""## 问题概览
当前检测到单进程内存占用偏高。

## 关键证据
已有工具证据显示 RSS 占用较高。

## 诊断发现
detector 识别到 memory_process_hogging。

## 建议操作
先检查进程，再处理危险操作。

## 审批状态
approval_id={approval_id} status=pending 尚未执行。

## 风险说明
危险操作仍是 pending，尚未执行。
"""


class FailingReportClient:
    def generate_report(self, prompt: str) -> str:
        raise RuntimeError("forced llm failure")


def approval_id_from_prompt(prompt: str) -> str:
    match = re.search(r"appr_[0-9a-f]+", prompt)
    return match.group(0) if match else "appr_unknown"


def configure_api(monkeypatch, tmp_path: Path, llm_client) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(tmp_path / "runs"))

    def build_fixture_agent(
        approval_service: ApprovalService,
        agent_mode: str | None = None,
        planner_mode: str | None = None,
        report_mode: str | None = None,
    ) -> ResourceAgent:
        return ResourceAgent(
            registry=MemoryPressureRegistry(),
            approval_service=approval_service,
            agent_mode=agent_mode,
            planner_mode=planner_mode,
            report_mode=report_mode,
            llm_client=llm_client,
        )

    monkeypatch.setattr(api, "build_resource_agent", build_fixture_agent)


def wait_for_report_status(client: TestClient, run_id: str, statuses: set[str], timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        trace = client.get(f"/runs/{run_id}").json()
        if trace["run"]["report_status"] in statuses:
            return trace
        time.sleep(0.05)
    raise AssertionError(f"report status did not reach {statuses}")


def test_diagnose_returns_202_before_llm_report_finishes(monkeypatch, tmp_path: Path) -> None:
    llm_client = BlockingReportClient()
    configure_api(monkeypatch, tmp_path, llm_client)

    with TestClient(app) as client:
        response = client.post(
            "/diagnose",
            json={"description": "为什么内存快满了？", "resource_type": "memory", "report_mode": "llm"},
        )
        assert response.status_code == 202
        payload = response.json()
        run_id = payload["run_id"]
        approval_id = payload["approvals"][0]["approval_id"]

        assert payload["report_status"] == "generating"
        assert payload["run_status"] == "waiting_approval"
        assert llm_client.started.wait(timeout=2)

        trace = client.get(f"/runs/{run_id}").json()
        assert trace["run"]["final_report"] is None
        approvals = client.get("/approvals").json()
        assert any(item["approval_id"] == approval_id for item in approvals)

        llm_client.release.set()
        wait_for_report_status(client, run_id, {"ready"})


def test_approval_during_report_generation_is_reflected_in_final_report(monkeypatch, tmp_path: Path) -> None:
    llm_client = BlockingReportClient()
    configure_api(monkeypatch, tmp_path, llm_client)

    with TestClient(app) as client:
        response = client.post(
            "/diagnose",
            json={"description": "为什么内存快满了？", "resource_type": "memory", "report_mode": "llm"},
        )
        payload = response.json()
        run_id = payload["run_id"]
        approval_id = payload["approvals"][0]["approval_id"]
        assert llm_client.started.wait(timeout=2)

        approve_response = client.post(f"/approvals/{approval_id}/approve")
        assert approve_response.status_code == 200
        assert approve_response.json()["approval"]["status"] == "executed"

        llm_client.release.set()
        trace = wait_for_report_status(client, run_id, {"ready"})

        assert trace["approvals"][0]["status"] == "executed"
        assert "status=executed" in trace["run"]["final_report"]
        assert "dry_run=success" in trace["run"]["final_report"]
        assert "status=pending 尚未执行" not in trace["run"]["final_report"]


def test_llm_report_failure_sets_fallback_status(monkeypatch, tmp_path: Path) -> None:
    configure_api(monkeypatch, tmp_path, FailingReportClient())

    with TestClient(app) as client:
        response = client.post(
            "/diagnose",
            json={"description": "为什么内存快满了？", "resource_type": "memory", "report_mode": "llm"},
        )
        run_id = response.json()["run_id"]
        trace = wait_for_report_status(client, run_id, {"fallback"})

        assert trace["run"]["report_status"] == "fallback"
        assert trace["run"]["final_report"]


def test_report_job_exception_sets_failed_status(monkeypatch, tmp_path: Path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    approval_store = ApprovalStore(trace_store=trace_store)
    approval_service = ApprovalService(store=approval_store)
    agent = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
        report_mode="template",
    )
    snapshot = agent.collect_and_detect(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))
    snapshot.run.report_status = ReportGenerationStatus.GENERATING
    trace_store.save_diagnosis_snapshot(snapshot)
    writer = WorkspaceWriter(tmp_path / "runs")
    writer.write_diagnosis_snapshot(snapshot)

    def fail_save_report_snapshot(_report):
        raise RuntimeError("forced save failure")

    monkeypatch.setattr(trace_store, "save_report_snapshot", fail_save_report_snapshot)

    try:
        run_report_job(
            agent=agent,
            snapshot=snapshot,
            trace_store=trace_store,
            workspace_writer=writer,
        )
    except RuntimeError:
        pass

    trace = trace_store.get_trace(snapshot.run.run_id)
    assert trace["run"]["report_status"] == "failed"
    assert "forced save failure" in trace["run"]["report_error"]


def test_recover_interrupted_report_jobs_marks_generating_as_failed(tmp_path: Path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    result = ResourceAgent(registry=MemoryPressureRegistry()).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )
    result.run.report_status = ReportGenerationStatus.GENERATING
    trace_store.save_agent_result(result)

    recover_interrupted_report_jobs(trace_store)

    trace = trace_store.get_trace(result.run.run_id)
    assert trace["run"]["report_status"] == "failed"
    assert trace["run"]["report_error"] == "service restarted during report generation"
