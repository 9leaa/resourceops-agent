import json

from agent.llm_report import build_report_prompt
from agent.report_context import build_report_context
from app.schemas import (
    DiagnosisFinding,
    EvidenceItem,
    Recommendation,
    ResourceType,
    ToolCallStatus,
    ToolPermissionLevel,
)
from tools.registry import ToolExecutionResult


def tool_result(name: str, data: dict) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=name,
        permission_level=ToolPermissionLevel.SAFE,
        status=ToolCallStatus.SUCCESS,
        data=data,
        preview=f"{name} preview",
        summary=f"{name} summary",
        latency_ms=4,
        validated_args={},
    )


def evidence(index: int, confidence: float = 0.8) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"ev_{index}",
        run_id="run_test",
        source_tool="list_top_memory_processes",
        category="process",
        level="warning",
        message=f"PID {index} uses a large amount of memory",
        data={
            "process": {
                "pid": index,
                "rss_mb": 20000 + index,
                "memory_percent": 62.5,
                "command": "python train.py --api-key sk-secretvalue",
            },
            "total_mb": 32000,
        },
        confidence=confidence,
    )


def finding(index: int, evidence_ids: list[str], confidence: float = 0.8) -> DiagnosisFinding:
    return DiagnosisFinding(
        finding_id=f"find_{index}",
        run_id="run_test",
        finding_type=f"memory_pressure_{index}",
        title=f"Memory pressure {index}",
        description="A deterministic memory finding.",
        evidence_ids=evidence_ids,
        confidence=confidence,
        recommended_actions=[
            Recommendation(
                action="kill_process",
                description="Terminate the confirmed process.",
                risk="dangerous",
                requires_approval=True,
                reason="The process owns most memory.",
            )
        ],
        requires_approval=True,
    )


def test_context_prioritizes_finding_evidence_and_deduplicates() -> None:
    items = [evidence(1, 0.4), evidence(2, 0.99), evidence(3, 0.7)]
    findings = [finding(1, ["ev_1", "ev_1"], 0.9)]

    context = build_report_context(
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=items,
        findings=findings,
        approvals=[],
    )

    assert [item["evidence_id"] for item in context["key_evidence"]] == ["ev_1", "ev_2", "ev_3"]


def test_context_limits_findings_and_evidence() -> None:
    items = [evidence(index, 1 - index / 100) for index in range(8)]
    findings = [finding(index, [f"ev_{index}"], 1 - index / 100) for index in range(5)]

    context = build_report_context(
        description="memory",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=items,
        findings=findings,
        approvals=[],
    )

    assert len(context["diagnosis"]["root_causes"]) == 3
    assert len(context["key_evidence"]) == 5
    assert context["provenance"]["context_truncated"] is True


def test_context_excludes_unrelated_process_lists_but_keeps_memory_metrics() -> None:
    context = build_report_context(
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[
            tool_result(
                "get_memory_snapshot",
                {"total_mb": 32000, "available_mb": 256, "used_percent": 96, "swap_used_percent": 60},
            ),
            tool_result(
                "list_top_cpu_processes",
                {"processes": [{"pid": 999, "command": "unrelated process", "cpu_percent": 20}]},
            ),
        ],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    serialized = json.dumps(context, ensure_ascii=False)
    assert context["system_summary"]["memory"]["used_percent"] == 96
    assert "unrelated process" not in serialized
    assert "tool_context" not in context


def test_context_keeps_approval_status_and_redacts_sensitive_values() -> None:
    item = evidence(12345)
    context = build_report_context(
        description="为什么内存快满了？ token=question-secret",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[item],
        findings=[finding(1, [item.evidence_id], 0.9)],
        approvals=[
            {
                "approval_id": "appr_test",
                "action": "kill_process",
                "status": "pending",
            }
        ],
    )

    serialized = json.dumps(context, ensure_ascii=False)
    assert context["recommendations"][0]["approval_id"] == "appr_test"
    assert context["recommendations"][0]["approval_status"] == "pending"
    assert "sk-secretvalue" not in serialized
    assert "question-secret" not in serialized
    assert "<redacted>" in serialized


def test_context_keeps_gpu_summary_and_ruled_out_state() -> None:
    context = build_report_context(
        description="为什么 GPU 显存满了？",
        resource_type=ResourceType.GPU,
        tool_results=[
            tool_result(
                "get_gpu_snapshot",
                {
                    "available": True,
                    "gpus": [
                        {
                            "index": 0,
                            "name": "NVIDIA RTX",
                            "utilization_gpu_percent": 12,
                            "memory_used_mb": 22000,
                            "memory_total_mb": 24576,
                            "memory_used_percent": 89.5,
                        }
                    ],
                },
            )
        ],
        evidence_items=[],
        findings=[],
        approvals=[],
    )

    assert context["system_summary"]["gpu"]["devices"][0]["memory_used_percent"] == 89.5
    assert context["ruled_out"][0]["condition"] == "gpu_pressure"
    assert context["ruled_out"][0]["matched"] is False


def test_context_serialized_size_is_bounded() -> None:
    items = [evidence(index) for index in range(10)]
    context = build_report_context(
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=items,
        findings=[finding(1, [item.evidence_id for item in items])],
        approvals=[],
    )

    assert len(json.dumps(context, ensure_ascii=False)) <= 5500
    prompt = build_report_prompt(report_context=context)
    assert len(prompt) <= 6000
    assert all(
        section in prompt
        for section in ("问题概览", "关键证据", "诊断发现", "建议操作", "审批状态", "风险说明")
    )
