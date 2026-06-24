from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult
from trace.store import TraceStore


class FixtureRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        if name == "get_memory_snapshot":
            data = {
                "total_mb": 32000,
                "available_mb": 512,
                "used_percent": 94,
                "swap_total_mb": 8192,
                "swap_used_percent": 55,
            }
        elif name == "list_top_memory_processes":
            data = {
                "processes": [
                    {
                        "pid": 123,
                        "rss_mb": 20000,
                        "memory_percent": 62.5,
                        "command": "python train.py",
                    }
                ]
            }
        elif name == "check_oom_events":
            data = {
                "available": True,
                "source": "dmesg",
                "events": ["Out of memory: Killed process 123 python"],
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


def test_trace_saves_p3_evidence_and_findings(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    result = ResourceAgent(registry=FixtureRegistry()).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )

    trace_store.save_agent_result(result)
    trace = trace_store.get_trace(result.run.run_id)

    assert len(trace["tool_calls"]) == len(result.tool_results)
    assert len(trace["evidence_items"]) > 0
    assert len(trace["findings"]) > 0

    finding_types = {finding["finding_type"] for finding in trace["findings"]}
    assert "memory_pressure" in finding_types
    assert "swap_pressure" in finding_types
    assert "oom_event" in finding_types