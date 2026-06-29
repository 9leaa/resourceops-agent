import json

from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult


class RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        self.calls.append((name, args or {}))
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


class FakePlannerClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        assert "工具目录 JSON" in user_prompt
        return json.dumps(self.payload, ensure_ascii=False)


def test_p9_agent_uses_valid_llm_plan_for_tool_execution() -> None:
    registry = RecordingRegistry()
    result = ResourceAgent(
        registry=registry,
        agent_mode="llm_planner",
        llm_client=FakePlannerClient(
            {
                "steps": [
                    {
                        "tool_name": "get_cpu_snapshot",
                        "args": {},
                        "reason": "先看 CPU 快照。",
                        "expected_result": "CPU load。",
                    }
                ]
            }
        ),
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert result.tool_plan is not None
    assert result.tool_plan.planner_mode == "llm"
    assert [name for name, _args in registry.calls] == ["get_cpu_snapshot"]
    assert [tool.tool_name for tool in result.tool_results] == ["get_cpu_snapshot"]

    llm_step = [step for step in result.steps if step.action == "llm_planner"][0]
    assert llm_step.observation["used_llm_plan"] is True
    assert "llm plan accepted" in llm_step.observation_preview

    plan_step = [step for step in result.steps if step.action == "build_tool_plan"][0]
    assert plan_step.observation["tool_plan"]["planner_mode"] == "llm"


def test_p9_agent_falls_back_when_llm_plan_is_invalid() -> None:
    registry = RecordingRegistry()
    result = ResourceAgent(
        registry=registry,
        agent_mode="llm_planner",
        llm_client=FakePlannerClient(
            {
                "steps": [
                    {
                        "tool_name": "unknown_tool",
                        "args": {},
                        "reason": "非法工具。",
                        "expected_result": "不应该执行。",
                    }
                ]
            }
        ),
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert result.tool_plan is not None
    assert result.tool_plan.planner_mode == "fallback"
    assert [name for name, _args in registry.calls] == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
        "get_memory_snapshot",
        "get_gpu_snapshot",
    ]

    llm_step = [step for step in result.steps if step.action == "llm_planner"][0]
    assert llm_step.observation["used_llm_plan"] is False
    assert llm_step.observation["fallback_reason"] == "plan_validation_failed"
    assert "unknown tool" in llm_step.observation["validation_errors"][0]
