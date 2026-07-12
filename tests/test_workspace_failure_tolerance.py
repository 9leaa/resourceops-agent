from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent.resource_agent import ResourceAgent
from app import api
from app.api import app
from app.report_jobs import run_report_job
from app.schemas import ReportGenerationStatus, ResourceIncident
from approval.service import ApprovalService
from approval.store import ApprovalStore
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


class FailingWorkspaceWriter:
    def __init__(self, *_args, **_kwargs) -> None:
        self.report_apply_calls = 0

    def write_diagnosis_snapshot(self, _snapshot) -> None:
        raise OSError("forced diagnosis workspace failure")

    def apply_report_snapshot(self, _report, trace_store: Any | None = None) -> None:
        self.report_apply_calls += 1
        raise OSError("forced report workspace failure")


def wait_for_report_status(client: TestClient, run_id: str, statuses: set[str], timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        trace = client.get(f"/runs/{run_id}").json()
        if trace["run"]["report_status"] in statuses:
            return trace
        time.sleep(0.05)
    raise AssertionError(f"report status did not reach {statuses}")


def test_workspace_failure_does_not_prevent_report_job_submission(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setattr(api, "WorkspaceWriter", FailingWorkspaceWriter)

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
        )

    monkeypatch.setattr(api, "build_resource_agent", build_fixture_agent)

    with TestClient(app) as client:
        response = client.post(
            "/diagnose",
            json={"description": "为什么 CPU 很高？", "resource_type": "cpu"},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        trace = wait_for_report_status(client, run_id, {"ready", "fallback"})

    assert trace["run"]["report_status"] in {"ready", "fallback"}
    assert trace["run"]["final_report"]


def test_workspace_report_update_failure_does_not_mark_report_failed(tmp_path: Path) -> None:
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    approval_store = ApprovalStore(trace_store=trace_store)
    approval_service = ApprovalService(store=approval_store)
    agent = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
        report_mode="template",
    )
    snapshot = agent.collect_and_detect(ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu"))
    snapshot.run.report_status = ReportGenerationStatus.GENERATING
    trace_store.save_diagnosis_snapshot(snapshot)

    writer = FailingWorkspaceWriter()
    run_report_job(
        agent=agent,
        snapshot=snapshot,
        trace_store=trace_store,
        workspace_writer=writer,
    )

    trace = trace_store.get_trace(snapshot.run.run_id)
    assert writer.report_apply_calls == 1
    assert trace["run"]["report_status"] == "ready"
    assert trace["run"]["final_report"]
