from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


def test_trace_saves_p4_approvals(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    approval_store = ApprovalStore(trace_store=trace_store)
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    trace_store.save_agent_result(result)
    trace = trace_store.get_trace(result.run.run_id)

    assert trace["run"]["status"] == "waiting_approval"
    assert len(trace["approvals"]) == 1
    assert trace["approvals"][0]["action"] == "kill_process"
    assert trace["approvals"][0]["status"] == "pending"
    assert "memory_pressure" in {finding["finding_type"] for finding in trace["findings"]}
