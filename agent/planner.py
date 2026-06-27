from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import (
    PlannedToolCall,
    PlannerMode,
    ResourceType,
    ToolCatalog,
    ToolCatalogItem,
    ToolPermissionLevel,
    ToolPlan,
)

@dataclass(frozen=True)
class PlannedAction:
    thought:str
    action:str
    args:dict[str, Any]


def infer_resource_type(description: str, explicit: ResourceType | str | None = None) -> ResourceType:
    if explicit:
        return ResourceType(explicit)

    text = description.lower()
    gpu_keywords = ("gpu", "cuda", "显存", "nvidia", "nvidia-smi")
    cpu_keywords = ("cpu", "load", "卡顿", "打满", "core")
    memory_keywords = ("memory", "内存", "swap", "oom", "out of memory")
    mixed_keywords = ("slow", "训练慢", "bottleneck", "瓶颈", "很慢", "慢")

    has_gpu = any(keyword in text for keyword in gpu_keywords)
    has_cpu = any(keyword in text for keyword in cpu_keywords)
    has_memory = any(keyword in text for keyword in memory_keywords)
    if sum([has_gpu, has_cpu, has_memory]) > 1:
        return ResourceType.MIXED
    if has_gpu:
        return ResourceType.GPU
    if has_cpu:
        return ResourceType.CPU
    if has_memory:
        return ResourceType.MEMORY
    if any(keyword in text for keyword in mixed_keywords):
        return ResourceType.MIXED
    return ResourceType.MIXED


def build_tool_plan(
    resource_type: ResourceType,
    user_question: str,
    tool_catalog: ToolCatalog,
    planner_mode: PlannerMode = PlannerMode.DETERMINISTIC,
) -> ToolPlan:
    """构建 P8 标准 ToolPlan。

    当前仍然复用 deterministic plan 的固定工具顺序，但输出升级为结构化计划。
    后续 LLM planner 也应该输出这个 ToolPlan，再交给同一套执行逻辑。
    """

    catalog_by_name = {tool.name: tool for tool in tool_catalog.tools}
    planned_actions = build_plan(resource_type)
    planned_calls: list[PlannedToolCall] = []

    for index, action in enumerate(planned_actions):
        catalog_item = catalog_by_name.get(action.action)
        if catalog_item is None:
            raise KeyError(f"planned tool is not in catalog: {action.action}")

        planned_calls.append(
            PlannedToolCall(
                step_index=index,
                tool_name=action.action,
                args=action.args,
                reason=action.thought,
                expected_result=expected_result_for_tool(action.action),
                permission_level=catalog_item.permission_level,
                requires_approval=catalog_item.requires_approval,
                required=True,
                tags=catalog_item.tags,
            )
        )

    return ToolPlan(
        planner_mode=planner_mode,
        resource_type=resource_type,
        user_question=user_question,
        steps=planned_calls,
        max_steps=len(planned_calls),
        budget={"max_tool_calls": len(planned_calls)},
        fallback_plan=[],
        tool_catalog_version=tool_catalog.catalog_version,
    )


def expected_result_for_tool(tool_name: str) -> str:
    expectations = {
        "get_cpu_snapshot": "CPU load、核心数、整体 CPU 使用率和单核归一化负载。",
        "list_top_cpu_processes": "当前 CPU 占用最高的进程列表。",
        "get_memory_snapshot": "系统内存、可用内存和 swap 使用情况。",
        "list_top_memory_processes": "RSS 内存占用最高的进程列表。",
        "check_oom_events": "近期内核 OOM 或 killed process 事件。",
        "get_gpu_snapshot": "GPU 是否可用、GPU 利用率、显存、温度和功耗。",
        "list_gpu_processes": "当前占用 GPU 显存的进程列表。",
        "inspect_process": "指定 PID 的进程详情。",
    }
    return expectations.get(tool_name, "该工具返回的资源诊断信息。")


def tool_plan_preview(plan: ToolPlan) -> str:
    return (
        f"tool_plan mode={plan.planner_mode} resource_type={plan.resource_type} "
        f"steps={len(plan.steps)}"
    )


def catalog_item_for_name(tool_catalog: ToolCatalog, name: str) -> ToolCatalogItem | None:
    for tool in tool_catalog.tools:
        if tool.name == name:
            return tool
    return None


def permission_for_planned_call(call: PlannedToolCall) -> ToolPermissionLevel:
    return ToolPermissionLevel(call.permission_level)


def build_plan(resource_type: ResourceType) -> list[PlannedAction]:
    if resource_type == ResourceType.GPU:
        return build_gpu_plan()
    if resource_type == ResourceType.CPU:
        return build_cpu_plan()
    if resource_type == ResourceType.MEMORY:
        return build_memory_plan()
    return build_mixed_plan()

def build_gpu_plan() -> list[PlannedAction]:
    return [
        PlannedAction(
            thought="检查 GPU 是否存在显存占用、利用率或 nvidia-smi 可用性问题。",
            action="get_gpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="列出占用 GPU 的进程，定位显存主要占用者。",
            action="list_gpu_processes",
            args={"limit": 50},
        ),
        PlannedAction(
            thought="检查 CPU 状态，判断 GPU 低利用率是否可能由 CPU 瓶颈导致。",
            action="get_cpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="检查系统内存和 swap，判断是否影响训练或数据加载。",
            action="get_memory_snapshot",
            args={},
        ),
        PlannedAction(
            thought="列出 CPU 占用最高的进程，辅助判断 dataloader 或预处理瓶颈。",
            action="list_top_cpu_processes",
            args={"limit": 10},
        ),
        PlannedAction(
            thought="列出内存占用最高的进程，辅助判断资源争抢。",
            action="list_top_memory_processes",
            args={"limit": 10},
        ),
    ]

def build_cpu_plan() -> list[PlannedAction]:
    return [
        PlannedAction(
            thought="检查 CPU load、核心数和整体 CPU 使用率。",
            action="get_cpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="列出 CPU 占用最高的进程，定位主要 CPU 消耗者。",
            action="list_top_cpu_processes",
            args={"limit": 10},
        ),
        PlannedAction(
            thought="检查内存和 swap，判断 CPU 高负载是否伴随内存压力。",
            action="get_memory_snapshot",
            args={},
        ),
        PlannedAction(
            thought="检查 GPU 状态，判断是否存在 CPU 高而 GPU 低利用率的训练瓶颈。",
            action="get_gpu_snapshot",
            args={},
        ),
    ]

def build_memory_plan() -> list[PlannedAction]:
    return [
        PlannedAction(
            thought="检查系统内存和 swap 使用情况。",
            action="get_memory_snapshot",
            args={},
        ),
        PlannedAction(
            thought="列出 RSS 最高的进程，定位内存主要占用者。",
            action="list_top_memory_processes",
            args={"limit": 10},
        ),
        PlannedAction(
            thought="检查近期 OOM 相关内核事件。",
            action="check_oom_events",
            args={"limit": 20},
        ),
        PlannedAction(
            thought="检查 CPU 状态，判断内存压力是否伴随高负载。",
            action="get_cpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="检查 GPU 状态，判断是否存在 GPU 训练任务相关内存压力。",
            action="get_gpu_snapshot",
            args={},
        ),
    ]

def build_mixed_plan() -> list[PlannedAction]:
    return [
        PlannedAction(
            thought="先检查 GPU 状态，判断训练慢是否与 GPU 利用率或显存相关。",
            action="get_gpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="检查 CPU 状态，判断是否存在 CPU / dataloader 瓶颈。",
            action="get_cpu_snapshot",
            args={},
        ),
        PlannedAction(
            thought="检查系统内存和 swap，判断是否存在内存压力。",
            action="get_memory_snapshot",
            args={},
        ),
        PlannedAction(
            thought="列出 GPU 进程，定位 GPU 资源占用者。",
            action="list_gpu_processes",
            args={"limit": 50},
        ),
        PlannedAction(
            thought="列出 CPU 占用最高的进程。",
            action="list_top_cpu_processes",
            args={"limit": 10},
        ),
        PlannedAction(
            thought="列出内存占用最高的进程。",
            action="list_top_memory_processes",
            args={"limit": 10},
        ),
        PlannedAction(
            thought="检查 OOM 事件，排除近期内存不足导致的异常。",
            action="check_oom_events",
            args={"limit": 20},
        ),
    ]
