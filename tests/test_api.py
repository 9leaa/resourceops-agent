import json

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
    assert response.status_code == 202
    payload = response.json()
    assert payload["resource_type"] == "memory"
    assert payload["run_status"] in {"running", "waiting_approval"}
    assert payload["report_status"] == "generating"


def test_full_http_approval_flow(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(tmp_path / "resourceops.sqlite3"))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(tmp_path / "approvals.jsonl"))
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(tmp_path / "runs"))
    monkeypatch.delenv("RESOURCEOPS_ENABLE_REAL_ACTIONS", raising=False)
    monkeypatch.delenv("RESOURCEOPS_REAL_ACTION_ALLOWLIST", raising=False)

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
    assert diagnose_response.status_code == 202
    diagnosis = diagnose_response.json()
    run_id = diagnosis["run_id"]
    approval_id = diagnosis["approvals"][0]["approval_id"]

    assert diagnosis["run_status"] == "waiting_approval"
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
    assert approved["action_result"]["mode"] == "dry_run"
    assert approved["action_result"]["status"] == "success"

    updated_trace_response = client.get(f"/runs/{run_id}")
    assert updated_trace_response.status_code == 200
    updated_trace = updated_trace_response.json()
    assert updated_trace["run"]["status"] == "completed"
    assert updated_trace["approvals"][0]["status"] == "executed"

    workspace_approvals = json.loads(
        (tmp_path / "runs" / run_id / "trace" / "approvals.json").read_text(encoding="utf-8")
    )
    workspace_todos = json.loads(
        (tmp_path / "runs" / run_id / "todos.json").read_text(encoding="utf-8")
    )
    approval_task = [todo for todo in workspace_todos if todo.get("approval_id") == approval_id][0]

    assert workspace_approvals[0]["status"] == "executed"
    assert approval_task["status"] == "completed"

    execute_real_response = client.post(
        f"/approvals/{approval_id}/execute-real",
        json={"confirm_real": True},
    )
    assert execute_real_response.status_code == 200
    execute_real = execute_real_response.json()
    assert execute_real["action_result"]["mode"] == "real"
    assert execute_real["action_result"]["status"] == "blocked"
    assert "real execution is disabled" in execute_real["action_result"]["error"]
    assert execute_real["tool_result"]["data"]["simulated"] is False

    final_trace_response = client.get(f"/runs/{run_id}")
    assert final_trace_response.status_code == 200
    final_trace = final_trace_response.json()
    assert [result["mode"] for result in final_trace["action_results"]] == ["dry_run", "real"]
    assert final_trace["action_results"][-1]["status"] == "blocked"

    workspace_action_results = json.loads(
        (tmp_path / "runs" / run_id / "trace" / "action_results.json").read_text(encoding="utf-8")
    )
    assert [result["mode"] for result in workspace_action_results] == ["dry_run", "real"]
    assert workspace_action_results[-1]["status"] == "blocked"
