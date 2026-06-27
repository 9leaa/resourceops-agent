from agent.tool_catalog import build_tool_catalog
from app.schemas import ResourceType
from tools.registry import default_registry


def test_default_registry_has_resource_tools() -> None:
    registry = default_registry()
    names = {tool["name"] for tool in registry.list_tools()}

    assert "get_cpu_snapshot" in names
    assert "list_top_cpu_processes" in names
    assert "get_memory_snapshot" in names
    assert "list_top_memory_processes" in names
    assert "check_oom_events" in names
    assert "get_gpu_snapshot" in names
    assert "list_gpu_processes" in names
    assert "inspect_process" in names


def test_registry_executes_cpu_tool() -> None:
    result = default_registry().execute("get_cpu_snapshot", {})
    assert result.status == "success"
    assert result.data["cpu_count"] > 0


def test_p8_tool_catalog_exports_llm_ready_tool_metadata() -> None:
    catalog = build_tool_catalog(default_registry())
    tools_by_name = {tool.name: tool for tool in catalog.tools}

    assert catalog.catalog_version == "p8"
    assert catalog.total_tools == len(catalog.tools)
    assert "get_cpu_snapshot" in tools_by_name
    assert "list_top_cpu_processes" in tools_by_name

    cpu_tool = tools_by_name["list_top_cpu_processes"]
    assert cpu_tool.permission_level == "safe"
    assert cpu_tool.requires_approval is False
    assert "cpu" in cpu_tool.tags
    assert "process" in cpu_tool.tags
    assert ResourceType.CPU in cpu_tool.resource_types
    assert ResourceType.MIXED in cpu_tool.resource_types
    assert "properties" in cpu_tool.input_schema
