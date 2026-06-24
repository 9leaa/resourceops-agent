from tools.cpu import GetCpuSnapshotInput, ListTopCpuProcessesInput, get_cpu_snapshot, list_top_cpu_processes


def test_get_cpu_snapshot() -> None:
    result = get_cpu_snapshot(GetCpuSnapshotInput())
    assert result["cpu_count"] > 0
    assert "load_avg_1m" in result
    assert "overall_cpu_percent" in result


def test_list_top_cpu_processes() -> None:
    result = list_top_cpu_processes(ListTopCpuProcessesInput(limit=5))
    assert "processes" in result
    assert len(result["processes"]) <= 5