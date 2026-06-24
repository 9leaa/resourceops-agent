from tools.memory import (
    CheckOomEventsInput,
    GetMemorySnapshotInput,
    ListTopMemoryProcessesInput,
    check_oom_events,
    get_memory_snapshot,
    list_top_memory_processes,
)


def test_get_memory_snapshot() -> None:
    result = get_memory_snapshot(GetMemorySnapshotInput())
    assert result["total_mb"] > 0
    assert "used_percent" in result
    assert "swap_used_percent" in result


def test_list_top_memory_processes() -> None:
    result = list_top_memory_processes(ListTopMemoryProcessesInput(limit=5))
    assert "processes" in result
    assert len(result["processes"]) <= 5


def test_check_oom_events_does_not_crash() -> None:
    result = check_oom_events(CheckOomEventsInput(limit=5))
    assert "available" in result
    assert "events" in result