from agent.planner import build_tool_plan
from agent.todos import (
    build_phase_todos,
    mark_todo_completed,
    mark_todo_failed,
    mark_todo_running,
    todos_from_tool_plan,
)
from agent.tool_catalog import build_tool_catalog
from app.schemas import ResourceType
from tools.registry import default_registry


def test_build_phase_todos() -> None:
    phases = build_phase_todos("run_test")

    assert [phase.title for phase in phases] == [
        "Planning tools",
        "Tool execution",
        "Report",
        "Approval",
        "Action execution",
    ]
    assert {phase.level for phase in phases} == {"phase"}
    assert phases[0].display_group == "planning"
    assert phases[1].display_group == "tools"


def test_todos_from_tool_plan() -> None:
    catalog = build_tool_catalog(default_registry())
    plan = build_tool_plan(
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        tool_catalog=catalog,
    )

    todos = todos_from_tool_plan("run_test", plan, parent_todo_id="todo_phase")

    assert len(todos) == len(plan.steps)
    assert todos[0].run_id == "run_test"
    assert todos[0].todo_index == 0
    assert todos[0].status == "pending"
    assert todos[0].level == "task"
    assert todos[0].parent_todo_id == "todo_phase"
    assert todos[0].display_group == "tools"
    assert todos[0].source == "tool_plan"
    assert todos[0].tool_name == "get_cpu_snapshot"
    assert todos[0].args == {}
    assert todos[0].planned_call_id == plan.steps[0].planned_call_id
    assert todos[0].assigned_agent == "resource_agent"


def test_todo_state_transitions() -> None:
    catalog = build_tool_catalog(default_registry())
    plan = build_tool_plan(
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        tool_catalog=catalog,
    )
    todo = todos_from_tool_plan("run_test", plan)[0]

    running = mark_todo_running(todo)
    completed = mark_todo_completed(running, "cpu=10%")
    failed = mark_todo_failed(running, "tool failed")

    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.result_preview == "cpu=10%"
    assert completed.error is None
    assert failed.status == "failed"
    assert failed.error == "tool failed"