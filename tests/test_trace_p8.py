from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult
from trace.store import TraceStore


class EmptyRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data={},
            preview=f"{name} fixture",
            summary=f"{name} fixture",
            latency_ms=0,
            validated_args=args or {},
        )


def test_p8_trace_saves_tool_plan_and_links_tool_calls_to_tool_steps(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    result = ResourceAgent(registry=EmptyRegistry()).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    trace_store.save_agent_result(result)
    trace = trace_store.get_trace(result.run.run_id)

    plan_steps = [step for step in trace["steps"] if step["action"] == "build_tool_plan"]
    assert len(plan_steps) == 1
    tool_plan = plan_steps[0]["observation"]["tool_plan"]
    assert tool_plan["planner_mode"] == "deterministic"
    assert [step["tool_name"] for step in tool_plan["steps"]] == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
        "get_memory_snapshot",
        "get_gpu_snapshot",
    ]

    step_by_id = {step["step_id"]: step for step in trace["steps"]}
    for tool_call in trace["tool_calls"]:
        linked_step = step_by_id[tool_call["step_id"]]
        assert linked_step["action"] == tool_call["tool_name"]
        assert linked_step["action"] != "build_tool_plan"
