from __future__ import annotations

from app.schemas import ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult


class MemoryPressureRegistry:
    """Fixture registry that reliably triggers memory findings and a kill approval."""

    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        if name == "get_memory_snapshot":
            data = {
                "total_mb": 32000,
                "available_mb": 256,
                "used_percent": 96,
                "swap_total_mb": 8192,
                "swap_used_percent": 60,
            }
        elif name == "list_top_memory_processes":
            data = {
                "processes": [
                    {
                        "pid": 12345,
                        "username": "zcj",
                        "rss_mb": 20000,
                        "vms_mb": 25000,
                        "memory_percent": 62.5,
                        "command": "python train.py",
                    }
                ]
            }
        elif name == "check_oom_events":
            data = {
                "available": True,
                "source": "dmesg",
                "events": [],
            }
        else:
            data = {}

        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data=data,
            preview=f"{name} fixture",
            summary=f"{name} fixture",
            latency_ms=0,
            validated_args=args or {},
        )
