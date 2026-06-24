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