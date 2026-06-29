import json

from agent.llm_planner import build_llm_tool_plan_result, parse_planner_json
from agent.plan_validator import PlanValidator
from agent.planner import build_tool_plan
from agent.tool_catalog import build_tool_catalog
from app.schemas import ResourceType
from tools.registry import default_registry


class FakePlannerClient:
    def __init__(self, payload: dict | str, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        if self.error:
            raise self.error
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def build_inputs():
    registry = default_registry()
    catalog = build_tool_catalog(registry)
    fallback_plan = build_tool_plan(
        resource_type=ResourceType.CPU,
        user_question="为什么 CPU 很高？",
        tool_catalog=catalog,
    )
    validator = PlanValidator(tool_catalog=catalog, registry=registry)
    return registry, catalog, fallback_plan, validator


def test_parse_planner_json_accepts_fenced_json() -> None:
    payload = parse_planner_json(
        """```json
{"steps": [{"tool_name": "get_cpu_snapshot", "args": {}}]}
```"""
    )

    assert payload["steps"][0]["tool_name"] == "get_cpu_snapshot"


def test_llm_planner_accepts_valid_plan() -> None:
    _registry, catalog, fallback_plan, validator = build_inputs()
    client = FakePlannerClient(
        {
            "steps": [
                {
                    "tool_name": "get_cpu_snapshot",
                    "args": {},
                    "reason": "先看 CPU 当前负载。",
                    "expected_result": "CPU load 和使用率。",
                },
                {
                    "tool_name": "list_top_cpu_processes",
                    "args": {"limit": 5},
                    "reason": "定位 CPU 占用最高的进程。",
                    "expected_result": "Top CPU 进程。",
                },
            ]
        }
    )

    result = build_llm_tool_plan_result(
        description="为什么 CPU 很高？",
        resource_type=ResourceType.CPU,
        tool_catalog=catalog,
        fallback_plan=fallback_plan,
        validator=validator,
        llm_client=client,
    )

    assert result.used_llm_plan is True
    assert result.tool_plan.planner_mode == "llm"
    assert [step.tool_name for step in result.tool_plan.steps] == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
    ]
    assert result.tool_plan.steps[1].args == {
        "limit": 5,
        "min_cpu_percent": 0.0,
    }
    assert result.tool_plan.fallback_plan == fallback_plan.steps
    assert client.last_user_prompt is not None
    assert "工具目录 JSON" in client.last_user_prompt


def test_llm_planner_falls_back_without_client() -> None:
    _registry, catalog, fallback_plan, validator = build_inputs()

    result = build_llm_tool_plan_result(
        description="为什么 CPU 很高？",
        resource_type=ResourceType.CPU,
        tool_catalog=catalog,
        fallback_plan=fallback_plan,
        validator=validator,
        llm_client=None,
    )

    assert result.used_llm_plan is False
    assert result.llm_called is False
    assert result.fallback_reason == "no_llm_client"
    assert result.tool_plan.planner_mode == "fallback"
    assert [step.tool_name for step in result.tool_plan.steps] == [
        "get_cpu_snapshot",
        "list_top_cpu_processes",
        "get_memory_snapshot",
        "get_gpu_snapshot",
    ]


def test_llm_planner_falls_back_on_invalid_plan() -> None:
    _registry, catalog, fallback_plan, validator = build_inputs()
    client = FakePlannerClient(
        {
            "steps": [
                {
                    "tool_name": "run_shell_command",
                    "args": {"command": "kill -9 1"},
                    "reason": "非法工具。",
                    "expected_result": "不应该执行。",
                }
            ]
        }
    )

    result = build_llm_tool_plan_result(
        description="为什么 CPU 很高？",
        resource_type=ResourceType.CPU,
        tool_catalog=catalog,
        fallback_plan=fallback_plan,
        validator=validator,
        llm_client=client,
    )

    assert result.used_llm_plan is False
    assert result.llm_called is True
    assert result.fallback_reason == "plan_validation_failed"
    assert any("unknown tool" in error for error in result.validation_errors or [])
    assert result.tool_plan.planner_mode == "fallback"
