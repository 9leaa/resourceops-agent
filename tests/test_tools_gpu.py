from tools.gpu import GetGpuSnapshotInput, ListGpuProcessesInput, get_gpu_snapshot, list_gpu_processes


def test_get_gpu_snapshot_does_not_crash() -> None:
    result = get_gpu_snapshot(GetGpuSnapshotInput())
    assert "available" in result
    assert "gpus" in result


def test_list_gpu_processes_does_not_crash() -> None:
    result = list_gpu_processes(ListGpuProcessesInput(limit=5))
    assert "available" in result
    assert "processes" in result