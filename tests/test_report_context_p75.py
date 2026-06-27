from agent.report_context import COMMAND_PREVIEW_LIMIT, build_report_context
from app.schemas import ResourceType, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult


def tool_result(name: str, data: dict) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=name,
        permission_level=ToolPermissionLevel.SAFE,
        status=ToolCallStatus.SUCCESS,
        data=data,
        preview=f"{name} preview",
        summary=f"{name} summary",
        latency_ms=0,
        validated_args={},
    )


def test_report_context_includes_top_cpu_process_details() -> None:
    context = build_report_context(
        description="为什么 CPU 很高？",
        resource_type=ResourceType.CPU,
        tool_results=[
            tool_result(
                "list_top_cpu_processes",
                {
                    "processes": [
                        {
                            "pid": 123,
                            "username": "zcj",
                            "cpu_percent": 180.5,
                            "memory_percent": 3.2,
                            "rss_mb": 512,
                            "command": "python train.py --config config.yaml",
                            "started_at": "2026-06-27T00:00:00Z",
                        }
                    ]
                },
            )
        ],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    top_process = context["tool_context"][0]["top_processes"][0]
    assert top_process["pid"] == 123
    assert top_process["cpu_percent"] == 180.5
    assert top_process["command_preview"] == "python train.py --config config.yaml"


def test_report_context_limits_and_truncates_process_commands() -> None:
    long_command = "python train.py " + ("--very-long-arg " * 40)
    processes = [
        {
            "pid": index,
            "username": "zcj",
            "rss_mb": 100 + index,
            "vms_mb": 200 + index,
            "memory_percent": index,
            "command": long_command,
        }
        for index in range(10)
    ]

    context = build_report_context(
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[tool_result("list_top_memory_processes", {"processes": processes})],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    tool_context = context["tool_context"][0]
    assert len(tool_context["top_processes"]) == 5
    assert tool_context["truncated"] is True
    assert len(tool_context["top_processes"][0]["command_preview"]) <= COMMAND_PREVIEW_LIMIT + 3


def test_report_context_redacts_sensitive_command_values() -> None:
    context = build_report_context(
        description="为什么 CPU 很高？",
        resource_type=ResourceType.CPU,
        tool_results=[
            tool_result(
                "list_top_cpu_processes",
                {
                    "processes": [
                        {
                            "pid": 123,
                            "username": "zcj",
                            "cpu_percent": 10,
                            "memory_percent": 1,
                            "rss_mb": 100,
                            "command": "python app.py --api-key sk-secretvalue token=abc123",
                        }
                    ]
                },
            )
        ],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    command = context["tool_context"][0]["top_processes"][0]["command_preview"]
    assert "sk-secretvalue" not in command
    assert "abc123" not in command
    assert "<redacted>" in command


def test_report_context_keeps_gpu_snapshot_details() -> None:
    context = build_report_context(
        description="为什么 GPU 显存满了？",
        resource_type=ResourceType.GPU,
        tool_results=[
            tool_result(
                "get_gpu_snapshot",
                {
                    "available": True,
                    "driver_version": "555.0",
                    "gpus": [
                        {
                            "index": 0,
                            "name": "NVIDIA RTX",
                            "utilization_gpu_percent": 12,
                            "memory_used_mb": 22000,
                            "memory_total_mb": 24576,
                            "memory_used_percent": 89.5,
                            "temperature_c": 55,
                        }
                    ],
                },
            )
        ],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    gpu = context["tool_context"][0]["gpu"]["gpus"][0]
    assert gpu["index"] == 0
    assert gpu["memory_used_percent"] == 89.5
    assert gpu["memory_used_mb"] == 22000
