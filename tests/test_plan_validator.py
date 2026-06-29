from agent.plan_validator import PlanValidator
from agent.planner import build_tool_plan
from agent.tool_catalog import build_tool_catalog
from app.schemas import PlannedToolCall, PlannerMode, ResourceType, ToolPlan
from tools.registry import default_registry


def make_validator() -> PlanValidator:
    registry = default_registry()
    return PlanValidator(
        tool_catalog=build_tool_catalog(registry),
        registry=registry,
        max_steps=4,
    )


def test_plan_validator_accepts_valid_plan_and_normalizes_args() -> None:
    registry = default_registry()
    catalog = build_tool_catalog(registry)
    plan = ToolPlan(
        planner_mode=PlannerMode.LLM,
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        steps=[
            PlannedToolCall(
                step_index=0,
                tool_name="list_top_cpu_processes",
                args={},
                reason="查看 CPU 占用最高的进程。",
            )
        ],
        max_steps=1,
        budget={"max_tool_calls": 1},
        tool_catalog_version=catalog.catalog_version,
    )

    result = PlanValidator(tool_catalog=catalog, registry=registry).validate(
        plan,
        expected_resource_type=ResourceType.CPU,
    )

    assert result.valid is True
    assert result.normalized_plan is not None
    assert result.normalized_plan.steps[0].args == {
        "limit": 10,
        "min_cpu_percent": 0.0,
    }
    assert result.normalized_plan.steps[0].permission_level == "safe"


def test_plan_validator_rejects_unknown_tool() -> None:
    plan = ToolPlan(
        planner_mode=PlannerMode.LLM,
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        steps=[
            PlannedToolCall(
                step_index=0,
                tool_name="run_shell_command",
                args={"command": "rm -rf /"},
                reason="非法工具。",
            )
        ],
        max_steps=1,
        budget={"max_tool_calls": 1},
    )

    result = make_validator().validate(plan, expected_resource_type=ResourceType.CPU)

    assert result.valid is False
    assert any("unknown tool" in error for error in result.errors)


def test_plan_validator_rejects_invalid_args() -> None:
    plan = ToolPlan(
        planner_mode=PlannerMode.LLM,
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        steps=[
            PlannedToolCall(
                step_index=0,
                tool_name="list_top_cpu_processes",
                args={"limit": 999},
                reason="参数超过 schema 上限。",
            )
        ],
        max_steps=1,
        budget={"max_tool_calls": 1},
    )

    result = make_validator().validate(plan, expected_resource_type=ResourceType.CPU)

    assert result.valid is False
    assert any("invalid args" in error for error in result.errors)


def test_deterministic_tool_plan_passes_validator() -> None:
    registry = default_registry()
    catalog = build_tool_catalog(registry)
    plan = build_tool_plan(
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        tool_catalog=catalog,
    )

    result = PlanValidator(tool_catalog=catalog, registry=registry).validate(
        plan,
        expected_resource_type=ResourceType.CPU,
    )

    assert result.valid is True
