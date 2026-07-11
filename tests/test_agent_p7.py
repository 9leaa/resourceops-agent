from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult


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


class CpuProcessRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        if name == "list_top_cpu_processes":
            data = {
                "processes": [
                    {
                        "pid": 4242,
                        "username": "zcj",
                        "cpu_percent": 180.5,
                        "memory_percent": 3.2,
                        "rss_mb": 512,
                        "command": "python train.py --config config.yaml",
                        "started_at": "2026-06-27T00:00:00Z",
                    }
                ]
            }
        elif name == "get_cpu_snapshot":
            data = {
                "cpu_count": 8,
                "load_avg_1m": 9.5,
                "load_avg_5m": 8.0,
                "load_avg_15m": 7.5,
                "load_per_cpu_1m": 1.18,
                "overall_cpu_percent": 91.2,
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


class FakeReportClient:
    def __init__(self) -> None:
        self.last_prompt: str | None = None

    def generate_report(self, prompt: str) -> str:
        self.last_prompt = prompt
        assert "诊断数据" in prompt
        return """## 问题概览
这是 LLM 报告。

## 关键证据
使用已有证据。

## 诊断发现
当前没有 detector 命中异常。

## 建议操作
继续观察资源状态。

## 审批状态
无待审批危险操作。

## 风险说明
不自动执行危险操作。
"""


class BrokenReportClient:
    def generate_report(self, prompt: str) -> str:
        raise RuntimeError("llm failed")


def test_p7_llm_report_mode_replaces_only_final_report() -> None:
    llm_client = FakeReportClient()
    result = ResourceAgent(
        registry=EmptyRegistry(),
        agent_mode="llm_report",
        llm_client=llm_client,
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert "这是 LLM 报告" in result.final_report
    assert result.run.final_report == result.final_report
    assert result.run.status == "completed"
    assert result.findings == []
    assert result.approvals == []
    assert result.requires_approval is False
    assert llm_client.last_prompt is not None
    assert "system_summary" in llm_client.last_prompt
    assert "tool_context" not in llm_client.last_prompt

    context_step = result.steps[-2]
    assert context_step.action == "build_report_context"
    assert context_step.observation["context_version"] == "p14"
    assert context_step.observation["provenance"]["source_tools"]

    llm_step = result.steps[-1]
    assert llm_step.action == "llm_report"
    assert llm_step.observation["used_llm"] is True
    assert llm_step.observation["status"] == "success"
    assert "llm report generated" in llm_step.observation_preview


def test_p7_llm_report_mode_falls_back_to_deterministic_report() -> None:
    result = ResourceAgent(
        registry=EmptyRegistry(),
        agent_mode="llm_report",
        llm_client=BrokenReportClient(),
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert "## Resource Diagnosis Report" in result.final_report
    assert result.run.status == "completed"
    assert result.findings == []
    assert result.approvals == []

    llm_step = result.steps[-1]
    assert llm_step.action == "llm_report"
    assert llm_step.observation["used_llm"] is False
    assert llm_step.observation["status"] == "fallback"
    assert llm_step.observation["fallback_reason"] == "RuntimeError"
    assert "fallback to deterministic report" in llm_step.observation_preview
    assert result.steps[-2].action == "build_report_context"


def test_p7_deterministic_mode_does_not_call_llm() -> None:
    class ExplodingClient:
        def generate_report(self, prompt: str) -> str:
            raise AssertionError("deterministic mode must not call llm")

    result = ResourceAgent(
        registry=EmptyRegistry(),
        agent_mode="deterministic",
        llm_client=ExplodingClient(),
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert "## Resource Diagnosis Report" in result.final_report
    assert result.run.status == "completed"
    assert all(step.action != "llm_report" for step in result.steps)


def test_p75_llm_prompt_includes_compact_cpu_process_context() -> None:
    llm_client = FakeReportClient()

    result = ResourceAgent(
        registry=CpuProcessRegistry(),
        agent_mode="llm_report",
        llm_client=llm_client,
    ).diagnose(
        ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu")
    )

    assert llm_client.last_prompt is not None
    assert '"pid": 4242' in llm_client.last_prompt
    assert "python train.py --config config.yaml" in llm_client.last_prompt

    context_step = [step for step in result.steps if step.action == "build_report_context"][0]
    assert any("4242" in str(item["facts"]) for item in context_step.observation["key_evidence"])
