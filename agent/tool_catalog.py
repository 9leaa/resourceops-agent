from __future__ import annotations

from app.schemas import ResourceType, ToolCatalog, ToolCatalogItem, ToolPermissionLevel
from tools.registry import ToolRegistry


def build_tool_catalog(registry: ToolRegistry) -> ToolCatalog:
    """从 ToolRegistry 生成给 planner / LLM 使用的工具目录。"""

    items = [
        ToolCatalogItem(
            name=tool["name"],
            description=tool["description"],
            input_schema=tool["input_schema"],
            permission_level=tool["permission_level"],
            requires_approval=tool["permission_level"] == ToolPermissionLevel.DANGEROUS.value,
            timeout_seconds=tool["timeout_seconds"],
            retry=tool["retry"],
            tags=tool["tags"],
            resource_types=resource_types_for_tags(tool["tags"]),
        )
        for tool in registry.list_tools()
    ]
    return ToolCatalog(tools=items, total_tools=len(items))


def resource_types_for_tags(tags: list[str]) -> list[ResourceType]:
    """根据工具标签推断这个工具适合的诊断范围。"""

    tag_set = set(tags)
    resource_types: list[ResourceType] = []

    if "cpu" in tag_set:
        resource_types.append(ResourceType.CPU)
    if "memory" in tag_set or "oom" in tag_set:
        resource_types.append(ResourceType.MEMORY)
    if "gpu" in tag_set:
        resource_types.append(ResourceType.GPU)
    if "process" in tag_set or len(resource_types) > 1:
        resource_types.append(ResourceType.MIXED)

    if not resource_types:
        resource_types.append(ResourceType.MIXED)

    return unique_resource_types(resource_types)


def unique_resource_types(resource_types: list[ResourceType]) -> list[ResourceType]:
    seen: set[ResourceType] = set()
    unique: list[ResourceType] = []
    for resource_type in resource_types:
        if resource_type in seen:
            continue
        seen.add(resource_type)
        unique.append(resource_type)
    return unique
