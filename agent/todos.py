from __future__ import annotations

from app.schemas import DiagnosisTodo, TodoDisplayGroup, TodoLevel, TodoStatus, ToolPlan, utc_now

PHASE_PLANNING_TOOLS = "Planning tools"
PHASE_TOOL_EXECUTION = "Tool execution"
PHASE_REPORT = "Report"
PHASE_APPROVAL = "Approval"
PHASE_ACTION_EXECUTION = "Action execution"


def build_phase_todos(run_id: str) -> list[DiagnosisTodo]:
    phases = [
        (PHASE_PLANNING_TOOLS, TodoDisplayGroup.PLANNING),
        (PHASE_TOOL_EXECUTION, TodoDisplayGroup.TOOLS),
        (PHASE_REPORT, TodoDisplayGroup.REPORT),
        (PHASE_APPROVAL, TodoDisplayGroup.APPROVAL),
        (PHASE_ACTION_EXECUTION, TodoDisplayGroup.ACTIONS),
    ]
    return [
        DiagnosisTodo(
            run_id=run_id,
            todo_index=index,
            sort_order=index,
            title=title,
            status=TodoStatus.PENDING,
            level=TodoLevel.PHASE,
            display_group=group,
            source="phase",
            assigned_agent="resource_agent",
        )
        for index, (title, group) in enumerate(phases)
    ]


def todos_from_tool_plan(run_id: str, tool_plan: ToolPlan, parent_todo_id: str | None = None, start_index: int = 0) -> list[DiagnosisTodo]:
    return [
        DiagnosisTodo(
            run_id=run_id,
            todo_index=start_index + index,
            sort_order=index,
            title=f"Run {planned.tool_name}",
            status=TodoStatus.PENDING,
            level=TodoLevel.TASK,
            parent_todo_id=parent_todo_id,
            display_group=TodoDisplayGroup.TOOLS,
            source="tool_plan",
            tool_name=planned.tool_name,
            args=planned.args,
            planned_call_id=planned.planned_call_id,
            assigned_agent="resource_agent",
        )
        for index, planned in enumerate(tool_plan.steps)
    ]


def todos_from_approvals(run_id: str, approvals: list[dict], parent_todo_id: str | None, start_index: int = 0) -> list[DiagnosisTodo]:
    return [
        DiagnosisTodo(
            run_id=run_id,
            todo_index=start_index + index,
            sort_order=index,
            title=f"Approval: {approval['action']}",
            status=TodoStatus.WAITING_APPROVAL,
            level=TodoLevel.TASK,
            parent_todo_id=parent_todo_id,
            display_group=TodoDisplayGroup.APPROVAL,
            source="approval",
            tool_name=approval["action"],
            args=approval.get("args") or {},
            approval_id=approval["approval_id"],
            assigned_agent="approval_service",
            result_preview=f"pending approval: {approval['approval_id']}",
        )
        for index, approval in enumerate(approvals)
    ]


def phase_by_group(phases: list[DiagnosisTodo], group: TodoDisplayGroup | str) -> tuple[int, DiagnosisTodo]:
    group_value = group.value if isinstance(group, TodoDisplayGroup) else group
    for index, phase in enumerate(phases):
        if phase.display_group == group_value:
            return index, phase
    raise KeyError(f"phase not found: {group_value}")


def mark_todo_running(todo: DiagnosisTodo) -> DiagnosisTodo:
    return todo.model_copy(update={"status": TodoStatus.RUNNING, "updated_at": utc_now()})


def mark_todo_completed(todo: DiagnosisTodo, result_preview: str | None = None) -> DiagnosisTodo:
    return todo.model_copy(update={"status": TodoStatus.COMPLETED, "result_preview": result_preview, "error": None, "updated_at": utc_now()})


def mark_todo_failed(todo: DiagnosisTodo, error: str | None = None) -> DiagnosisTodo:
    return todo.model_copy(update={"status": TodoStatus.FAILED, "error": error or "task failed", "updated_at": utc_now()})


def mark_todo_skipped(todo: DiagnosisTodo, result_preview: str | None = None) -> DiagnosisTodo:
    return todo.model_copy(update={"status": TodoStatus.SKIPPED, "result_preview": result_preview, "updated_at": utc_now()})


def mark_todo_waiting_approval(todo: DiagnosisTodo, result_preview: str | None = None) -> DiagnosisTodo:
    return todo.model_copy(update={"status": TodoStatus.WAITING_APPROVAL, "result_preview": result_preview, "updated_at": utc_now()})