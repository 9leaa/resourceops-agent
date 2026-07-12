from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tests.fixtures import MemoryPressureRegistry
from tools.registry import ToolExecutionResult
from trace.store import TraceStore


class SuccessfulRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data={},
            preview=f"{name} fixture",
            summary=f"{name} fixture",
            latency_ms=0,
            validated_args=args or {},
        )


def test_p10_trace_saves_and_loads_layered_todos(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    result = ResourceAgent(registry=SuccessfulRegistry()).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    trace_store.save_agent_result(result)
    trace = trace_store.get_trace(result.run.run_id)

    assert len(trace["todos"]) == len(result.todos)

    phases = [todo for todo in trace["todos"] if todo["level"] == "phase"]
    tasks = [todo for todo in trace["todos"] if todo["level"] == "task"]

    assert [phase["title"] for phase in phases] == [
        "Planning tools",
        "Tool execution",
        "Report",
        "Approval",
        "Action execution",
    ]
    assert [todo["tool_name"] for todo in tasks] == [step.tool_name for step in result.tool_plan.steps]
    assert {todo["status"] for todo in tasks} == {"completed"}
    assert tasks[0]["args"] == {}
    assert tasks[0]["depends_on"] == []
    assert tasks[0]["parent_todo_id"] == phases[1]["todo_id"]
    assert tasks[0]["display_group"] == "tools"
    assert tasks[0]["result_preview"]


def test_p10_approval_sync_updates_todo_status(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    approval_store = ApprovalStore(trace_store=trace_store)
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))

    trace_store.save_agent_result(result)
    approval_id = result.approvals[0]["approval_id"]

    approval, _tool_result = approval_service.approve(approval_id)
    sync_approval_trace(trace_store, approval_store, approval)

    trace = trace_store.get_trace(result.run.run_id)
    approval_phase = [
        todo for todo in trace["todos"]
        if todo["level"] == "phase" and todo["display_group"] == "approval"
    ][0]
    approval_task = [todo for todo in trace["todos"] if todo.get("approval_id") == approval_id][0]

    assert trace["run"]["status"] == "completed"
    assert approval_phase["status"] == "completed"
    assert approval_task["status"] == "completed"
    assert "executed" in approval_task["result_preview"]
