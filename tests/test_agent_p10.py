from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tests.fixtures import MemoryPressureRegistry
from tools.registry import ToolExecutionResult


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


class FailingCpuProcessRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        if name == "list_top_cpu_processes":
            return ToolExecutionResult(
                tool_name=name,
                permission_level=ToolPermissionLevel.SAFE,
                status=ToolCallStatus.ERROR,
                data=None,
                preview="tool error",
                summary="fixture failure",
                error="fixture failure",
                latency_ms=0,
                validated_args=args or {},
            )

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


def test_p10_agent_returns_completed_tool_todos_and_phase_todos() -> None:
    result = ResourceAgent(registry=SuccessfulRegistry()).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    phases = [todo for todo in result.todos if todo.level == "phase"]
    tool_todos = [todo for todo in result.todos if todo.level == "task"]

    assert [phase.title for phase in phases] == [
        "Planning tools",
        "Tool execution",
        "Report",
        "Approval",
        "Action execution",
    ]
    assert len(tool_todos) == len(result.tool_plan.steps)
    assert [todo.tool_name for todo in tool_todos] == [step.tool_name for step in result.tool_plan.steps]
    assert {todo.status for todo in tool_todos} == {"completed"}
    assert all(todo.parent_todo_id for todo in tool_todos)
    assert all(todo.result_preview for todo in tool_todos)

    phase_by_group = {phase.display_group: phase for phase in phases}
    assert phase_by_group["planning"].status == "completed"
    assert phase_by_group["tools"].status == "completed"
    assert phase_by_group["report"].status == "completed"
    assert phase_by_group["approval"].status == "completed"
    assert phase_by_group["actions"].status == "skipped"


def test_p10_agent_marks_failed_tool_todo_failed() -> None:
    result = ResourceAgent(registry=FailingCpuProcessRegistry()).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    tool_todos = [todo for todo in result.todos if todo.level == "task"]
    failed = [todo for todo in tool_todos if todo.tool_name == "list_top_cpu_processes"][0]

    assert failed.status == "failed"
    assert failed.error == "fixture failure"

    completed = [todo for todo in tool_todos if todo.tool_name != "list_top_cpu_processes"]
    assert completed
    assert {todo.status for todo in completed} == {"completed"}

    tools_phase = [todo for todo in result.todos if todo.level == "phase" and todo.display_group == "tools"][0]
    assert tools_phase.status == "failed"


def test_p10_agent_creates_approval_task_todos(tmp_path) -> None:
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))

    approval_phase = [
        todo for todo in result.todos
        if todo.level == "phase" and todo.display_group == "approval"
    ][0]
    approval_tasks = [todo for todo in result.todos if todo.source == "approval"]

    assert approval_phase.status == "waiting_approval"
    assert approval_tasks
    assert approval_tasks[0].status == "waiting_approval"
    assert approval_tasks[0].approval_id == result.approvals[0]["approval_id"]
