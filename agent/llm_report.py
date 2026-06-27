from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.llm_client import LlmClient
from agent.report_context import build_report_context
from app.schemas import DiagnosisFinding, EvidenceItem, ResourceType
from tools.registry import ToolExecutionResult


REQUIRED_SECTIONS = ("问题概览", "关键证据", "诊断发现", "建议操作", "审批状态", "风险说明")


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
    error_type: str | None = None
    error: str | None = None

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
            "error_type": self.error_type,
            "error": self.error,
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
    try:
        report = llm_client.generate_report(prompt).strip()
        validate_llm_report(report, approvals)
        return LlmReportResult(
            final_report=report,
            source="llm",
            status="success",
            prompt_length=len(prompt),
            response_length=len(report),
            response_preview=preview_text(report),
        )
    except Exception as exc:
        return LlmReportResult(
            final_report=deterministic_report,
            source="deterministic",
            status="fallback",
            fallback_reason=exc.__class__.__name__,
            prompt_length=len(prompt),
            response_length=len(report),
            response_preview=preview_text(report) if report else None,
            error_type=exc.__class__.__name__,
            error=str(exc),
        )


def build_report_prompt(*, report_context: dict[str, Any]) -> str:
    return f"""请根据下面 JSON 生成中文 Markdown 诊断报告。

硬性规则：
1. 只能使用 JSON 中已有事实，不能编造证据。
2. 不能新增工具调用结果。
3. 不能新增危险操作。
4. 不能把 pending approval 写成已执行。
5. 如果有 pending approval，必须明确说明危险操作尚未执行。
6. 必须包含这些章节：问题概览、关键证据、诊断发现、建议操作、审批状态、风险说明。
7. tool_context 是经过代码筛选、限量和脱敏后的上下文；不要声称没有明细，除非 tool_context 中确实没有对应字段。
8. evidence_items 和 findings 是确定性诊断结果；如果 tool_context 和 findings 表达不同，优先以 findings 为诊断结论，并用 tool_context 做解释补充。

诊断上下文 JSON：
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

    if pending_approvals and not contains_any(normalized, ("尚未执行", "未执行", "待审批", "pending")):
        raise LlmReportValidationError("pending approval execution state is unclear")

    if pending_approvals and "已执行" in normalized and not contains_any(normalized, ("尚未执行", "未执行")):
        raise LlmReportValidationError("pending approval was described as executed")


def contains_any(text: str, candidates: tuple[str, ...]) -> bool:
    return any(candidate in text for candidate in candidates)


def preview_text(text: str, limit: int = 500) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
