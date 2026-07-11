import json
import tarfile

from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult
from workspace.writer import WorkspaceWriter


class FakeLlmClient:
    model = "fake-model"
    temperature = 0.1

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {
                "steps": [
                    {
                        "tool_name": "get_cpu_snapshot",
                        "args": {},
                        "reason": "Collect CPU state.",
                        "expected_result": "CPU metrics.",
                    }
                ]
            }
        )

    def generate_report(self, prompt: str) -> str:
        return """## 问题概览
CPU diagnosis.

## 关键证据
No threshold matched.

## 诊断发现
No deterministic finding.

## 建议操作
Continue monitoring.

## 审批状态
No approvals.

## 风险说明
No action was executed.
"""


class CpuRegistry:
    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data={"overall_cpu_percent": 20, "cpu_count": 8},
            preview="cpu=20%",
            summary="CPU fixture",
            latency_ms=3,
            validated_args=args or {},
        )


def build_result():
    return ResourceAgent(
        registry=CpuRegistry(),
        planner_mode="llm",
        report_mode="llm",
        llm_client=FakeLlmClient(),
    ).diagnose(ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu"))


def test_llm_calls_record_latency_hashes_and_no_full_payload_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("RESOURCEOPS_STORE_LLM_PAYLOADS", raising=False)
    result = build_result()
    run_dir = WorkspaceWriter(tmp_path / "runs").write_agent_result(result)

    calls = json.loads((run_dir / "compact" / "llm_calls_summary.json").read_text(encoding="utf-8"))["calls"]
    assert [call["purpose"] for call in calls] == ["planner", "report"]
    assert all(call["latency_ms"] > 0 for call in calls)
    assert all(len(call["prompt_sha256"]) == 64 for call in calls)
    assert all(call["full_payload_stored"] is False for call in calls)
    assert not (run_dir / "raw" / "llm_calls.jsonl").exists()


def test_full_llm_payload_requires_flag_and_bundle_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESOURCEOPS_STORE_LLM_PAYLOADS", "true")
    result = build_result()
    writer = WorkspaceWriter(tmp_path / "runs")
    run_dir = writer.write_agent_result(result)
    assert (run_dir / "raw" / "llm_calls.jsonl").exists()

    default_bundle = writer.create_bundle(result.run.run_id, tmp_path / "bundles-default")
    included_bundle = writer.create_bundle(
        result.run.run_id,
        tmp_path / "bundles-full",
        include_llm_payloads=True,
    )
    with tarfile.open(default_bundle, "r:gz") as archive:
        assert all(not name.endswith("raw/llm_calls.jsonl") for name in archive.getnames())
    with tarfile.open(included_bundle, "r:gz") as archive:
        assert any(name.endswith("raw/llm_calls.jsonl") for name in archive.getnames())
