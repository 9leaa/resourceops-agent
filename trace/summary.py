from __future__ import annotations

from datetime import datetime
from typing import Any


SUMMARY_VERSION = "v1"


def build_run_summary(trace: dict[str, Any]) -> dict[str, Any]:
    run = trace.get("run") or {}
    steps = trace.get("steps") or []
    tool_calls = trace.get("tool_calls") or []
    findings = trace.get("findings") or []
    evidence = trace.get("evidence_items") or []
    approvals = trace.get("approvals") or []
    action_results = trace.get("action_results") or []

    planner = _planner_summary(steps)
    llm_steps = [step for step in steps if step.get("action") in {"llm_planner", "llm_report"}]
    approval_counts = _count_by_status(approvals)
    action_counts = _count_by_status(action_results)
    changed_system_state = any(
        bool((item.get("execution") or {}).get("changed_system_state")) for item in action_results
    )

    warnings: list[str] = []
    if action_results:
        warnings.append("report_generated_before_action_execution")
    if planner["fallback_reason"]:
        warnings.append(f"planner_fallback:{planner['fallback_reason']}")
    if any(step.get("error") for step in steps):
        warnings.append("step_errors_present")

    return {
        "summary_version": SUMMARY_VERSION,
        "run": {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "resource_type": run.get("resource_type"),
            "user_input": run.get("user_input"),
            "started_at": run.get("started_at"),
            "ended_at": run.get("ended_at"),
            "duration_ms": _duration_ms(run.get("started_at"), run.get("ended_at")),
        },
        "planning": planner,
        "execution": {
            "tool_count": len(tool_calls),
            "successful_tools": sum(1 for item in tool_calls if item.get("status") == "success"),
            "failed_tools": sum(1 for item in tool_calls if item.get("status") != "success"),
            "total_tool_latency_ms": sum(int(item.get("latency_ms") or 0) for item in tool_calls),
        },
        "diagnosis": {
            "root_cause": run.get("root_cause"),
            "finding_count": len(findings),
            "evidence_count": len(evidence),
            "top_findings": [
                {
                    "finding_type": item.get("finding_type"),
                    "title": item.get("title"),
                    "confidence": item.get("confidence"),
                }
                for item in sorted(findings, key=lambda item: float(item.get("confidence") or 0), reverse=True)[:3]
            ],
        },
        "approval": {
            "count": len(approvals),
            "pending": approval_counts.get("pending", 0),
            "executed": approval_counts.get("executed", 0),
            "rejected": approval_counts.get("rejected", 0),
        },
        "actions": {
            "dry_run_count": sum(1 for item in action_results if item.get("mode") == "dry_run"),
            "real_execution_count": sum(1 for item in action_results if item.get("mode") == "real"),
            "successful_count": action_counts.get("success", 0),
            "failed_count": len(action_results) - action_counts.get("success", 0),
            "changed_system_state": changed_system_state,
        },
        "llm": {
            "call_count": len(llm_steps),
            "planner_latency_ms": _llm_latency(llm_steps, "llm_planner"),
            "report_latency_ms": _llm_latency(llm_steps, "llm_report"),
            "fallback_count": sum(
                1 for step in llm_steps if (step.get("observation") or {}).get("fallback_reason")
            ),
        },
        "report_snapshot_stage": "diagnosis",
        "remediation_summary_available": bool(action_results or any(item.get("status") == "rejected" for item in approvals)),
        "warnings": warnings,
    }


def render_run_summary_markdown(summary: dict[str, Any]) -> str:
    run = summary["run"]
    planning = summary["planning"]
    execution = summary["execution"]
    diagnosis = summary["diagnosis"]
    approval = summary["approval"]
    actions = summary["actions"]
    llm = summary["llm"]

    lines = [
        f"# Run Summary: {run.get('run_id')}",
        "",
        "## Run",
        f"- Status: {run.get('status')}",
        f"- Resource: {run.get('resource_type')}",
        f"- Question: {run.get('user_input')}",
        f"- Duration: {run.get('duration_ms')} ms",
        "",
        "## Planning",
        f"- Mode/source: {planning.get('planner_mode')} / {planning.get('source')}",
        f"- Accepted: {planning.get('accepted')}",
        f"- Tools: {', '.join(planning.get('selected_tools') or []) or 'none'}",
        "",
        "## Execution",
        f"- Tools: {execution.get('successful_tools')} succeeded, {execution.get('failed_tools')} failed",
        f"- Tool latency: {execution.get('total_tool_latency_ms')} ms",
        "",
        "## Diagnosis",
        f"- Root cause: {diagnosis.get('root_cause') or 'none'}",
        f"- Findings/evidence: {diagnosis.get('finding_count')} / {diagnosis.get('evidence_count')}",
    ]
    for finding in diagnosis.get("top_findings") or []:
        lines.append(
            f"- {finding.get('finding_type')}: {finding.get('title')} "
            f"(confidence={finding.get('confidence')})"
        )
    lines.extend(
        [
            "",
            "## Approval And Actions",
            f"- Approvals: {approval.get('pending')} pending, {approval.get('executed')} executed, "
            f"{approval.get('rejected')} rejected",
            f"- Actions: {actions.get('dry_run_count')} dry-run, "
            f"{actions.get('real_execution_count')} real, changed_system_state={actions.get('changed_system_state')}",
            "",
            "## LLM",
            f"- Calls/fallbacks: {llm.get('call_count')} / {llm.get('fallback_count')}",
            f"- Planner/report latency: {llm.get('planner_latency_ms')} / {llm.get('report_latency_ms')} ms",
        ]
    )
    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines).rstrip() + "\n"


def render_run_summary_console(summary: dict[str, Any]) -> None:
    print(render_run_summary_markdown(summary).rstrip())


def _planner_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    planner_step = next((step for step in steps if step.get("action") == "llm_planner"), None)
    build_step = next((step for step in steps if step.get("action") == "build_tool_plan"), None)
    planner_observation = (planner_step or {}).get("observation") or {}
    build_observation = (build_step or {}).get("observation") or {}
    plan = build_observation.get("tool_plan") or planner_observation.get("selected_plan") or {}
    selected_tools = [item.get("tool_name") for item in plan.get("steps") or [] if item.get("tool_name")]
    source = planner_observation.get("source") or plan.get("planner_mode") or "deterministic"
    return {
        "planner_mode": plan.get("planner_mode") or source,
        "source": source,
        "accepted": bool(planner_observation.get("used_llm_plan")) if planner_step else True,
        "fallback_reason": planner_observation.get("fallback_reason"),
        "selected_tools": selected_tools,
    }


def _llm_latency(steps: list[dict[str, Any]], action: str) -> int:
    step = next((item for item in steps if item.get("action") == action), None)
    if not step:
        return 0
    observation = step.get("observation") or {}
    return int(observation.get("latency_ms") or step.get("latency_ms") or 0)


def _count_by_status(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _duration_ms(started_at: Any, ended_at: Any) -> int | None:
    start = _parse_datetime(started_at)
    end = _parse_datetime(ended_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
