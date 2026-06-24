from agent.planner import build_plan
from app.schemas import ResourceType


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