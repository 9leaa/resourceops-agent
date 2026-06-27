from agent.planner import build_plan, build_tool_plan
from agent.tool_catalog import build_tool_catalog
from app.schemas import ResourceType
from tools.registry import default_registry


def actions_for(resource_type: ResourceType) -> list[str]:
    return [item.action for item in build_plan(resource_type)]


def test_gpu_plan() -> None:
    actions = actions_for(ResourceType.GPU)
    assert actions == [
        "get_gpu_snapshot",
        "list_gpu_processes",
        "get_cpu_snapshot",
        "get_memory_snapshot",
        "list_top_cpu_processes",
        "list_top_memory_processes",
    ]


def test_cpu_plan() -> None:
    actions = actions_for(ResourceType.CPU)
    assert actions == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
        "get_memory_snapshot",
        "get_gpu_snapshot",
    ]


def test_memory_plan() -> None:
    actions = actions_for(ResourceType.MEMORY)
    assert actions == [
        "get_memory_snapshot",
        "list_top_memory_processes",
        "check_oom_events",
        "get_cpu_snapshot",
        "get_gpu_snapshot",
    ]


def test_mixed_plan() -> None:
    actions = actions_for(ResourceType.MIXED)
    assert actions == [
        "get_gpu_snapshot",
        "get_cpu_snapshot",
        "get_memory_snapshot",
        "list_gpu_processes",
        "list_top_cpu_processes",
        "list_top_memory_processes",
        "check_oom_events",
    ]


def test_p8_tool_plan_wraps_deterministic_plan() -> None:
    catalog = build_tool_catalog(default_registry())
    plan = build_tool_plan(
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        tool_catalog=catalog,
    )

    assert plan.planner_mode == "deterministic"
    assert plan.resource_type == ResourceType.CPU
    assert plan.user_question == "为什么 CPU 很高？"
    assert plan.tool_catalog_version == "p8"
    assert plan.max_steps == 4
    assert plan.budget["max_tool_calls"] == 4
    assert [step.tool_name for step in plan.steps] == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
        "get_memory_snapshot",
        "get_gpu_snapshot",
    ]
    assert plan.steps[0].reason
    assert plan.steps[0].expected_result
    assert plan.steps[0].permission_level == "safe"
    assert plan.steps[0].requires_approval is False
