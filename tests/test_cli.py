from app.cli import main
from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


def test_json_only_cli(capsys) -> None:
    exit_code = main(["diagnose", "为什么 CPU 很高？", "--json-only"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "为什么 CPU 很高？" in captured.out


def test_trace_cli_prints_approvals(monkeypatch, capsys) -> None:
    class FakeTraceStore:
        def get_trace(self, run_id: str) -> dict:
            assert run_id == "run_test"
            return {
                "run": {
                    "run_id": "run_test",
                    "status": "waiting_approval",
                    "resource_type": "memory",
                    "user_input": "为什么内存快满了？",
                    "summary": "has one approval",
                },
                "steps": [
                    {
                        "step_index": 0,
                        "action": "infer_resource_type",
                        "observation_preview": "resource_type=memory",
                    }
                ],
                "findings": [
                    {
                        "finding_type": "memory_pressure",
                        "confidence": 0.9,
                    }
                ],
                "approvals": [
                    {
                        "approval_id": "appr_test",
                        "action": "kill_process",
                        "status": "pending",
                        "risk": "dangerous",
                    }
                ],
            }

    monkeypatch.setattr("app.cli.TraceStore", FakeTraceStore)

    exit_code = main(["trace", "run_test"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "approvals:" in captured.out
    assert "appr_test kill_process status=pending risk=dangerous" in captured.out


def test_cli_approve_syncs_trace(monkeypatch, tmp_path, capsys) -> None:
    trace_db = tmp_path / "resourceops.sqlite3"
    approval_path = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(trace_db))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(approval_path))

    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    trace_store.save_agent_result(result)

    approval_id = result.approvals[0]["approval_id"]

    exit_code = main(["approve", approval_id])
    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)

    assert exit_code == 0
    assert "已批准并模拟执行" in captured.out
    assert trace["run"]["status"] == "completed"
    assert trace["approvals"][0]["status"] == "executed"
