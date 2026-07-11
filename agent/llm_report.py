from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from agent.llm_client import LlmClient
from agent.report_context import build_report_context
from app.schemas import DiagnosisFinding, EvidenceItem, ResourceType
from tools.registry import ToolExecutionResult
from trace.llm_calls import build_llm_call_record


REQUIRED_SECTIONS = ("问题概览", "关键证据", "诊断发现", "建议操作", "审批状态", "风险说明")
SYSTEM_PROMPT = (
    "你是 ResourceOps 的诊断报告撰写器。"
    "输入数据已经由确定性工具和 Detector 生成。"
    "你只能重组和解释输入中的事实，不能新增事实、工具结果、命令或操作。"
    "必须准确区分：发现、建议、待审批、dry-run、真实执行。"
)


class LlmReportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LlmReportResult:
    final_report: str
    source: str
    status: str
    fallback_reason: str | None = None
    prompt_length: int = 0
    response_length: int = 0
    response_preview: str | None = None
    latency_ms: int = 0
    error_type: str | None = None
    error: str | None = None
    llm_call: dict[str, Any] | None = None

    @property
    def used_llm(self) -> bool:
        return self.source == "llm" and self.status == "success"

    @property
    def preview(self) -> str:
        if self.used_llm:
            return f"llm report generated; response_length={self.response_length}"
        if self.fallback_reason:
            return f"fallback to deterministic report: {self.fallback_reason}"
        return "fallback to deterministic report"

    def model_dump(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "used_llm": self.used_llm,
            "fallback_reason": self.fallback_reason,
            "prompt_length": self.prompt_length,
            "response_length": self.response_length,
            "response_preview": self.response_preview,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error": self.error,
            "llm_call": self.llm_call,
        }


def build_llm_report(
    *,
    deterministic_report: str,
    description: str,
    resource_type: ResourceType,
    tool_results: list[ToolExecutionResult],
    evidence_items: list[EvidenceItem],
    findings: list[DiagnosisFinding],
    approvals: list[dict[str, Any]],
    llm_client: LlmClient | None,
    report_context: dict[str, Any] | None = None,
) -> str:
    return build_llm_report_result(
        deterministic_report=deterministic_report,
        description=description,
        resource_type=resource_type,
        tool_results=tool_results,
        evidence_items=evidence_items,
        findings=findings,
        approvals=approvals,
        llm_client=llm_client,
        report_context=report_context,
    ).final_report


def build_llm_report_result(
    *,
    deterministic_report: str,
    description: str,
    resource_type: ResourceType,
    tool_results: list[ToolExecutionResult],
    evidence_items: list[EvidenceItem],
    findings: list[DiagnosisFinding],
    approvals: list[dict[str, Any]],
    llm_client: LlmClient | None,
    report_context: dict[str, Any] | None = None,
) -> LlmReportResult:
    if llm_client is None:
        return LlmReportResult(
            final_report=deterministic_report,
            source="deterministic",
            status="fallback",
            fallback_reason="no_llm_client",
        )

    context = report_context or build_report_context(
        description=description,
        resource_type=resource_type,
        tool_results=tool_results,
        evidence_items=evidence_items,
        findings=findings,
        approvals=approvals,
    )
    prompt = build_report_prompt(report_context=context)

    report = ""
    started = time.perf_counter()
    try:
        report = llm_client.generate_report(prompt).strip()
        validate_llm_report(report, approvals)
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return LlmReportResult(
            final_report=report,
            source="llm",
            status="success",
            prompt_length=len(prompt),
            response_length=len(report),
            response_preview=preview_text(report),
            latency_ms=latency_ms,
            llm_call=build_llm_call_record(
                purpose="report",
                client=llm_client,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                response=report,
                status="success",
                latency_ms=latency_ms,
            ),
        )
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return LlmReportResult(
            final_report=deterministic_report,
            source="deterministic",
            status="fallback",
            fallback_reason=exc.__class__.__name__,
            prompt_length=len(prompt),
            response_length=len(report),
            response_preview=preview_text(report) if report else None,
            latency_ms=latency_ms,
            error_type=exc.__class__.__name__,
            error=str(exc),
            llm_call=build_llm_call_record(
                purpose="report",
                client=llm_client,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                response=report,
                status="failed",
                latency_ms=latency_ms,
                error_type=exc.__class__.__name__,
                error=str(exc),
            ),
        )


def build_report_prompt(*, report_context: dict[str, Any]) -> str:
    return f"""请根据下面的结构化诊断数据生成中文 Markdown 报告。

要求：
1. 只使用输入 JSON 中的事实。
2. 重点解释 diagnosis.root_causes 与 key_evidence。
3. system_summary 用于说明当前资源状态。
4. ruled_out 只做简短排除说明，不展开无关细节。
5. recommendations 必须保持原审批和执行状态；pending 审批必须写出 approval_id、pending 和“尚未执行”。
6. 不生成 JSON 中不存在的命令、PID、数值或操作。
7. 每节最多 2 条；诊断发现最多 3 条。相同 PID、数值和状态只出现一次，不在不同章节重复解释。
8. 报告控制在 700～1100 个中文字符左右；问题概览只概括结论，关键证据写数值，其他章节不要复述数值。

必须包含：问题概览、关键证据、诊断发现、建议操作、审批状态、风险说明。

诊断数据：
```json
{json.dumps(report_context, ensure_ascii=False, indent=2)}
```
"""


def validate_llm_report(report: str, approvals: list[dict[str, Any]]) -> None:
    normalized = report.strip()
    if not normalized:
        raise LlmReportValidationError("empty llm report")

    missing = [section for section in REQUIRED_SECTIONS if section not in normalized]
    if missing:
        raise LlmReportValidationError(f"missing report sections: {missing}")

    pending_approvals = [item for item in approvals if item.get("status") == "pending"]

    for approval in pending_approvals:
        approval_id = str(approval.get("approval_id") or "")
        if approval_id and approval_id not in normalized:
            raise LlmReportValidationError(f"pending approval not mentioned: {approval_id}")

        approval_context = _approval_context(normalized, approval_id)
        if not contains_any(approval_context, ("pending", "待审批", "尚未执行", "未执行")):
            raise LlmReportValidationError(f"pending approval state is unclear: {approval_id}")
        if _describes_executed(approval_context):
            raise LlmReportValidationError(f"pending approval was described as executed: {approval_id}")

    if pending_approvals and not contains_any(normalized, ("尚未执行", "未执行", "待审批", "pending")):
        raise LlmReportValidationError("pending approval execution state is unclear")


def contains_any(text: str, candidates: tuple[str, ...]) -> bool:
    return any(candidate in text for candidate in candidates)


def _approval_context(report: str, approval_id: str, radius: int = 500) -> str:
    if not approval_id:
        return report
    position = report.find(approval_id)
    if position < 0:
        return ""
    return report[max(0, position - radius) : position + len(approval_id) + radius]


def _describes_executed(text: str) -> bool:
    return bool(
        re.search(
            r"(?:审批状态|状态|status)\s*(?:为|is)?\s*[:=：]?\s*(?:已执行|executed)",
            text,
            flags=re.IGNORECASE,
        )
    )


def preview_text(text: str, limit: int = 500) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
