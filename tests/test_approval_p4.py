from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry


def test_p4_creates_approval_for_dangerous_recommendation(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    assert result.run.status == "waiting_approval"
    assert result.requires_approval is True
    assert result.approvals

    approval = result.approvals[0]
    assert approval["action"] == "kill_process"
    assert approval["status"] == "pending"
    assert approval["risk"] == "dangerous"

    pending = approval_store.list(status="pending")
    assert len(pending) == 1
    assert pending[0].approval_id == approval["approval_id"]


def test_p4_approve_simulates_dangerous_action(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    approval_id = result.approvals[0]["approval_id"]
    approval, tool_result = approval_service.approve(approval_id)

    assert approval.status == "executed"
    assert tool_result.status == "success"
    assert tool_result.data["simulated"] is True
    assert tool_result.tool_name == "kill_process"


def test_p4_reject_keeps_action_unexecuted(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    approval_id = result.approvals[0]["approval_id"]
    approval = approval_service.reject(approval_id)

    assert approval.status == "rejected"
    assert approval.executed_at is None
