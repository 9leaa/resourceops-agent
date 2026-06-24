from app.schemas import ToolCallStatus, ToolPermissionLevel
from agent.detectors import run_detectors
from tools.registry import ToolExecutionResult


def tool_result(name: str, data: dict) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=name,
        permission_level=ToolPermissionLevel.SAFE,
        status=ToolCallStatus.SUCCESS,
        data=data,
        preview="fixture",
        summary="fixture",
        latency_ms=0,
        validated_args={},
    )


def finding_types(results):
    _evidence, findings = run_detectors("run_test", results)
    return {finding.finding_type for finding in findings}


def test_detect_gpu_memory_pressure() -> None:
    results = [
        tool_result(
            "get_gpu_snapshot",
            {
                "available": True,
                "gpus": [
                    {
                        "index": 0,
                        "memory_used_percent": 95,
                        "memory_used_mb": 23000,
                        "memory_total_mb": 24576,
                        "utilization_gpu_percent": 80,
                    }
                ],
            },
        ),
        tool_result(
            "list_gpu_processes",
            {
                "available": True,
                "processes": [
                    {
                        "pid": 123,
                        "used_memory_mb": 22000,
                        "username": "zcj",
                        "command": "python train.py",
                    }
                ],
            },
        ),
    ]

    assert "gpu_memory_pressure" in finding_types(results)


def test_detect_cpu_saturation() -> None:
    results = [
        tool_result(
            "get_cpu_snapshot",
            {
                "cpu_count": 8,
                "load_avg_1m": 12.0,
                "overall_cpu_percent": 88.0,
            },
        )
    ]

    assert "cpu_saturation" in finding_types(results)


def test_detect_memory_pressure_and_swap() -> None:
    results = [
        tool_result(
            "get_memory_snapshot",
            {
                "total_mb": 32000,
                "available_mb": 512,
                "used_percent": 94,
                "swap_total_mb": 8192,
                "swap_used_percent": 55,
            },
        )
    ]

    types = finding_types(results)
    assert "memory_pressure" in types
    assert "swap_pressure" in types


def test_detect_oom_event() -> None:
    results = [
        tool_result(
            "check_oom_events",
            {
                "available": True,
                "source": "dmesg",
                "events": ["Out of memory: Killed process 12345 python"],
            },
        )
    ]

    assert "oom_event" in finding_types(results)


def test_detect_gpu_unavailable() -> None:
    results = [
        tool_result(
            "get_gpu_snapshot",
            {
                "available": False,
                "error": "nvidia-smi not found",
                "gpus": [],
            },
        )
    ]

    assert "gpu_unavailable" in finding_types(results)