from __future__ import annotations

import re
from typing import Any

from app.schemas import DiagnosisFinding, EvidenceItem, ResourceType
from tools.registry import ToolExecutionResult


PROCESS_LIMIT = 5
GPU_PROCESS_LIMIT = 10
OOM_EVENT_LIMIT = 3
COMMAND_PREVIEW_LIMIT = 160
OOM_EVENT_PREVIEW_LIMIT = 240
CONTEXT_VERSION = "p7.5"
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
    tool_context = [compact_tool_result(result) for result in tool_results]
    return {
        "context_version": CONTEXT_VERSION,
        "user_question": description,
        "resource_type": resource_type.value,
        "tool_context": tool_context,
        "evidence_items": [item.model_dump(mode="json") for item in evidence_items],
        "findings": [item.model_dump(mode="json") for item in findings],
        "approvals": approvals,
        "source_tools": [result.tool_name for result in tool_results],
        "counts": {
            "tools": len(tool_results),
            "evidence_items": len(evidence_items),
            "findings": len(findings),
            "approvals": len(approvals),
        },
        "redaction": {
            "command_preview_limit": COMMAND_PREVIEW_LIMIT,
            "process_limit": PROCESS_LIMIT,
            "gpu_process_limit": GPU_PROCESS_LIMIT,
            "oom_event_limit": OOM_EVENT_LIMIT,
        },
    }


def report_context_preview(context: dict[str, Any]) -> str:
    counts = context.get("counts") or {}
    return (
        f"report_context tools={counts.get('tools', 0)} "
        f"evidence={counts.get('evidence_items', 0)} "
        f"findings={counts.get('findings', 0)} "
        f"approvals={counts.get('approvals', 0)}"
    )


def compact_tool_result(result: ToolExecutionResult) -> dict[str, Any]:
    base = {
        "tool_name": result.tool_name,
        "status": result.status,
        "permission_level": result.permission_level,
        "preview": result.preview,
        "summary": result.summary,
        "error": result.error,
    }

    data = result.data if isinstance(result.data, dict) else {}

    if result.tool_name == "get_cpu_snapshot":
        base["cpu"] = pick(
            data,
            [
                "cpu_count",
                "load_avg_1m",
                "load_avg_5m",
                "load_avg_15m",
                "load_per_cpu_1m",
                "overall_cpu_percent",
            ],
        )
        return base

    if result.tool_name == "list_top_cpu_processes":
        processes = data.get("processes") or []
        base["top_processes"] = [compact_process(item, include_cpu=True) for item in processes[:PROCESS_LIMIT]]
        base["total_processes_seen"] = len(processes)
        base["truncated"] = len(processes) > PROCESS_LIMIT
        return base

    if result.tool_name == "get_memory_snapshot":
        base["memory"] = pick(
            data,
            [
                "total_mb",
                "available_mb",
                "used_mb",
                "used_percent",
                "swap_total_mb",
                "swap_used_mb",
                "swap_used_percent",
            ],
        )
        return base

    if result.tool_name == "list_top_memory_processes":
        processes = data.get("processes") or []
        base["top_processes"] = [compact_process(item, include_memory=True) for item in processes[:PROCESS_LIMIT]]
        base["total_processes_seen"] = len(processes)
        base["truncated"] = len(processes) > PROCESS_LIMIT
        return base

    if result.tool_name == "check_oom_events":
        events = data.get("events") or []
        base["oom"] = {
            "available": data.get("available"),
            "source": data.get("source"),
            "events": [preview_text(str(item), OOM_EVENT_PREVIEW_LIMIT) for item in events[:OOM_EVENT_LIMIT]],
            "total_events_seen": len(events),
            "truncated": len(events) > OOM_EVENT_LIMIT,
            "reason": data.get("reason"),
        }
        return base

    if result.tool_name == "get_gpu_snapshot":
        gpus = data.get("gpus") or []
        base["gpu"] = {
            "available": data.get("available"),
            "driver_version": data.get("driver_version"),
            "cuda_version": data.get("cuda_version"),
            "gpus": [
                pick(
                    item,
                    [
                        "index",
                        "name",
                        "driver_version",
                        "utilization_gpu_percent",
                        "memory_used_mb",
                        "memory_total_mb",
                        "memory_used_percent",
                        "temperature_c",
                        "power_draw_w",
                    ],
                )
                for item in gpus
            ],
            "error": data.get("error"),
        }
        return base

    if result.tool_name == "list_gpu_processes":
        processes = data.get("processes") or []
        base["gpu_processes"] = [
            {
                "pid": item.get("pid"),
                "gpu_uuid": item.get("gpu_uuid"),
                "process_name": item.get("process_name"),
                "used_memory_mb": item.get("used_memory_mb"),
                "username": item.get("username"),
                "command_preview": command_preview(item.get("command")),
            }
            for item in processes[:GPU_PROCESS_LIMIT]
        ]
        base["total_processes_seen"] = len(processes)
        base["truncated"] = len(processes) > GPU_PROCESS_LIMIT
        base["available"] = data.get("available")
        return base

    if result.tool_name == "inspect_process":
        memory_info = data.get("memory_info") or {}
        base["process"] = {
            "available": data.get("available"),
            "pid": data.get("pid"),
            "ppid": data.get("ppid"),
            "username": data.get("username"),
            "status": data.get("status"),
            "cpu_percent": data.get("cpu_percent"),
            "rss_mb": memory_info.get("rss_mb"),
            "vms_mb": memory_info.get("vms_mb"),
            "create_time": data.get("create_time"),
            "num_threads": data.get("num_threads"),
            "children_count": len(data.get("children") or []),
            "command_preview": command_preview(data.get("cmdline")),
            "error": data.get("error"),
        }
        return base

    if data:
        base["data_preview"] = preview_value(data)
    return base


def compact_process(
    item: dict[str, Any],
    *,
    include_cpu: bool = False,
    include_memory: bool = False,
) -> dict[str, Any]:
    compact = {
        "pid": item.get("pid"),
        "username": item.get("username"),
        "command_preview": command_preview(item.get("command")),
    }
    if include_cpu:
        compact.update(
            {
                "cpu_percent": item.get("cpu_percent"),
                "memory_percent": item.get("memory_percent"),
                "rss_mb": item.get("rss_mb"),
                "started_at": item.get("started_at"),
            }
        )
    if include_memory:
        compact.update(
            {
                "rss_mb": item.get("rss_mb"),
                "vms_mb": item.get("vms_mb"),
                "memory_percent": item.get("memory_percent"),
            }
        )
    return compact


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


def preview_value(value: Any) -> str:
    return preview_text(str(value), 300)
