from fastapi.testclient import TestClient

from agent.resource_agent import ResourceAgent
from app import api
from app.api import app
from approval.service import ApprovalService
from tests.fixtures import MemoryPressureRegistry


def test_health(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(tmp_path / "approvals.jsonl"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_diagnose_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(tmp_path / "approvals.jsonl"))
    client = TestClient(app)
    response = client.post("/diagnose", json={"description": "为什么内存快满了？", "resource_type": "memory"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["resource_type"] == "memory"
    assert payload["run"]["status"] in {"completed", "waiting_approval"}


def test_full_http_approval_flow(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(tmp_path / "approvals.jsonl"))

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

    client = TestClient(app)
    diagnose_response = client.post(
        "/diagnose",
        json={"description": "为什么内存快满了？", "resource_type": "memory"},
    )
    assert diagnose_response.status_code == 200
    diagnosis = diagnose_response.json()
    run_id = diagnosis["run"]["run_id"]
    approval_id = diagnosis["approvals"][0]["approval_id"]

    assert diagnosis["run"]["status"] == "waiting_approval"
    assert diagnosis["requires_approval"] is True

    runs_response = client.get("/runs")
    assert runs_response.status_code == 200
    assert any(run["run_id"] == run_id for run in runs_response.json())

    trace_response = client.get(f"/runs/{run_id}")
    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["run"]["status"] == "waiting_approval"
    assert trace["approvals"][0]["approval_id"] == approval_id

    approvals_response = client.get("/approvals")
    assert approvals_response.status_code == 200
    assert any(item["approval_id"] == approval_id for item in approvals_response.json())

    approve_response = client.post(f"/approvals/{approval_id}/approve")
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["approval"]["status"] == "executed"
    assert approved["tool_result"]["data"]["simulated"] is True

    updated_trace_response = client.get(f"/runs/{run_id}")
    assert updated_trace_response.status_code == 200
    updated_trace = updated_trace_response.json()
    assert updated_trace["run"]["status"] == "completed"
    assert updated_trace["approvals"][0]["status"] == "executed"
