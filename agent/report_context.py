from __future__ import annotations

import json
import re
from typing import Any

from app.schemas import DiagnosisFinding, EvidenceItem, ResourceType
from tools.registry import ToolExecutionResult


CONTEXT_VERSION = "p14"
MAX_FINDINGS = 3
MAX_EVIDENCE = 5
COMMAND_PREVIEW_LIMIT = 160
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(--?(?:api[-_]?key|token|password|passwd|secret)(?:=|\s+))(\S+)"),
    re.compile(r"(?i)((?:api[-_]?key|token|password|passwd|secret)=)(\S+)"),
    re.compile(r"(?i)(bearer\s+)(\S+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b"),
)


def build_report_context(
    *,
    description: str,
    resource_type: ResourceType,
    tool_results: list[ToolExecutionResult],
    evidence_items: list[EvidenceItem],
    findings: list[DiagnosisFinding],
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_findings = select_key_findings(findings)
    selected_evidence = select_key_evidence(selected_findings, evidence_items)
    system_summary = build_system_summary(resource_type, tool_results)
    root_causes = build_root_causes(selected_findings, selected_evidence)
    context: dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "incident": {
            "question": redact_sensitive_text(description),
            "resource_type": resource_type.value,
        },
        "diagnosis": {
            "status": "finding_detected" if root_causes else "no_threshold_match",
            "root_causes": root_causes,
        },
        "key_evidence": [compact_evidence(item) for item in selected_evidence],
        "system_summary": system_summary,
        "ruled_out": build_ruled_out_summary(tool_results),
        "recommendations": build_recommendation_summary(selected_findings, approvals),
        "provenance": {
            "source_tools": [result.tool_name for result in tool_results],
            "evidence_count_total": len(evidence_items),
            "findings_count_total": len(findings),
            "context_truncated": len(findings) > MAX_FINDINGS or len(evidence_items) > MAX_EVIDENCE,
        },
    }
    if not root_causes:
        context["key_observations"] = build_key_observations(system_summary)
    return context


def build_incident_context(description: str, resource_type: ResourceType) -> dict[str, Any]:
    return {"question": redact_sensitive_text(description), "resource_type": resource_type.value}


def select_key_findings(
    findings: list[DiagnosisFinding],
    limit: int = MAX_FINDINGS,
) -> list[DiagnosisFinding]:
    return sorted(findings, key=lambda item: float(item.confidence), reverse=True)[:limit]


def select_key_evidence(
    findings: list[DiagnosisFinding],
    evidence_items: list[EvidenceItem],
    limit: int = MAX_EVIDENCE,
) -> list[EvidenceItem]:
    by_id = {item.evidence_id: item for item in evidence_items}
    ordered: list[EvidenceItem] = []
    seen: set[str] = set()
    for finding in findings:
        for evidence_id in finding.evidence_ids:
            item = by_id.get(evidence_id)
            if item is not None and item.evidence_id not in seen:
                ordered.append(item)
                seen.add(item.evidence_id)
    for item in sorted(evidence_items, key=lambda value: float(value.confidence), reverse=True):
        if item.evidence_id not in seen:
            ordered.append(item)
            seen.add(item.evidence_id)
    return ordered[:limit]


def build_root_causes(
    findings: list[DiagnosisFinding],
    evidence_items: list[EvidenceItem],
) -> list[dict[str, Any]]:
    evidence_by_id = {item.evidence_id: item for item in evidence_items}
    causes: list[dict[str, Any]] = []
    for finding in findings:
        levels = [
            evidence_by_id[evidence_id].level
            for evidence_id in finding.evidence_ids
            if evidence_id in evidence_by_id
        ]
        causes.append(
            {
                "finding_id": finding.finding_id,
                "finding_type": finding.finding_type,
                "title": finding.title,
                "description": finding.description,
                "severity": levels[0] if levels else "warning",
                "confidence": finding.confidence,
            }
        )
    return causes


def compact_evidence(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "source_tool": item.source_tool,
        "level": item.level,
        "message": redact_sensitive_text(item.message),
        "confidence": item.confidence,
        "facts": compact_evidence_facts(item.data),
    }


def compact_evidence_facts(data: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    process = data.get("process")
    if isinstance(process, dict):
        facts["process"] = _redact_value(
            pick(
                process,
                ["pid", "username", "rss_mb", "vms_mb", "memory_percent", "cpu_percent", "command"],
            )
        )
    for key in (
        "total_mb",
        "gpu_index",
        "gpu_name",
        "memory_used_mb",
        "memory_total_mb",
        "memory_used_percent",
        "utilization_gpu_percent",
        "event_count",
    ):
        if key in data:
            facts[key] = _redact_value(data[key])
    if not facts:
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                facts[key] = _redact_value(value)
            if len(facts) >= 8:
                break
    return facts


def build_system_summary(
    resource_type: ResourceType,
    tool_results: list[ToolExecutionResult],
) -> dict[str, Any]:
    summary: dict[str, Any] = {"resource_type": resource_type.value}
    for result in tool_results:
        data = result.data if isinstance(result.data, dict) else {}
        if result.tool_name == "get_memory_snapshot":
            summary["memory"] = pick(
                data,
                ["total_mb", "available_mb", "used_mb", "used_percent", "swap_used_mb", "swap_used_percent"],
            )
        elif result.tool_name == "get_cpu_snapshot":
            summary["cpu"] = pick(
                data,
                ["cpu_count", "load_avg_1m", "load_per_cpu_1m", "overall_cpu_percent"],
            )
        elif result.tool_name == "get_gpu_snapshot":
            gpus = data.get("gpus") or []
            summary["gpu"] = {
                "available": data.get("available"),
                "devices": [
                    pick(
                        gpu,
                        ["index", "name", "utilization_gpu_percent", "memory_used_mb", "memory_total_mb", "memory_used_percent"],
                    )
                    for gpu in gpus[:4]
                ],
            }
        elif result.tool_name == "check_oom_events":
            summary["oom"] = {
                "available": data.get("available"),
                "source": data.get("source"),
                "event_count": len(data.get("events") or []),
            }
    return _redact_value(summary)


def build_ruled_out_summary(tool_results: list[ToolExecutionResult]) -> list[dict[str, Any]]:
    ruled_out: list[dict[str, Any]] = []
    for result in tool_results:
        data = result.data if isinstance(result.data, dict) else {}
        if result.tool_name == "get_cpu_snapshot" and "overall_cpu_percent" in data:
            value = float(data.get("overall_cpu_percent") or 0)
            ruled_out.append(
                {
                    "condition": "cpu_saturation",
                    "matched": value >= 90,
                    "reason": f"overall_cpu_percent={value}",
                }
            )
        elif result.tool_name == "get_gpu_snapshot":
            gpus = data.get("gpus") or []
            utilization = max((float(item.get("utilization_gpu_percent") or 0) for item in gpus), default=0)
            memory = max((float(item.get("memory_used_percent") or 0) for item in gpus), default=0)
            ruled_out.append(
                {
                    "condition": "gpu_pressure",
                    "matched": utilization >= 90 or memory >= 90,
                    "reason": f"max_utilization_percent={utilization}, max_memory_used_percent={memory}",
                }
            )
        elif result.tool_name == "check_oom_events":
            count = len(data.get("events") or [])
            ruled_out.append(
                {
                    "condition": "recent_oom",
                    "matched": count > 0,
                    "reason": f"event_count={count}",
                }
            )
    return ruled_out


def build_recommendation_summary(
    findings: list[DiagnosisFinding],
    approvals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    approval_by_action = {str(item.get("action")): item for item in approvals}
    recommendations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        for recommendation in finding.recommended_actions:
            key = (recommendation.action, recommendation.reason)
            if key in seen:
                continue
            seen.add(key)
            approval = approval_by_action.get(recommendation.action) or {}
            recommendations.append(
                {
                    "action": recommendation.action,
                    "description": recommendation.description,
                    "reason": recommendation.reason,
                    "risk": recommendation.risk,
                    "requires_approval": recommendation.requires_approval,
                    "approval_id": approval.get("approval_id"),
                    "approval_status": approval.get("status"),
                }
            )
    return recommendations


def build_key_observations(system_summary: dict[str, Any]) -> list[str]:
    observations: list[str] = []
    for section, values in system_summary.items():
        if section == "resource_type" or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                observations.append(f"{section}.{key}={value}")
    return observations[:8]


def report_context_preview(context: dict[str, Any]) -> str:
    diagnosis = context.get("diagnosis") or {}
    return (
        f"report_context version={context.get('context_version')} "
        f"root_causes={len(diagnosis.get('root_causes') or [])} "
        f"evidence={len(context.get('key_evidence') or [])} "
        f"recommendations={len(context.get('recommendations') or [])} "
        f"chars={len(json.dumps(context, ensure_ascii=False))}"
    )


def pick(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if key in data}


def command_preview(command: Any, limit: int = COMMAND_PREVIEW_LIMIT) -> str | None:
    if command is None:
        return None
    return preview_text(redact_sensitive_text(str(command)), limit)


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}<redacted>", redacted)
    return redacted


def preview_text(text: str, limit: int) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value
