from __future__ import annotations

import re
from typing import Any

from app.schemas import DiagnosisTodo, ReportSnapshot, RunStatus, TodoDisplayGroup


DYNAMIC_SECTION_TITLES = {"审批状态", "风险说明"}
DYNAMIC_SECTION_RE = re.compile(r"(?m)^##\s*(?:审批状态|风险说明)\s*$")


def report_static_prefix(report_text: str) -> str:
    """Return the streamable report prefix before dynamic approval/action sections."""

    match = DYNAMIC_SECTION_RE.search(report_text)
    if match is None:
        return report_text
    return report_text[: match.start()].rstrip()


def reconcile_report_snapshot_with_trace(report: ReportSnapshot, trace: dict[str, Any]) -> ReportSnapshot:
    """Rewrite report-owned dynamic state from the latest trace state.

    LLM report generation can run while the user is approving actions. The prompt
    therefore may contain stale approval status. Approval/action status is an
    auditable state machine, so the final report must take it from trace instead
    of trusting the natural-language LLM output. The same rule applies to
    run_status and todos so the final console/workspace view has one state
    source.
    """

    updates: dict[str, Any] = {
        "run_status": latest_run_status(trace, fallback=report.run_status),
        "todos": reconcile_report_todos_with_trace(report.todos, trace),
    }

    if trace.get("approvals") or trace.get("action_results"):
        updates["final_report"] = reconcile_report_text_with_trace(report.final_report, trace)

    return report.model_copy(update=updates)


def latest_run_status(trace: dict[str, Any], *, fallback: RunStatus) -> RunStatus:
    raw = (trace.get("run") or {}).get("status")
    try:
        return RunStatus(raw)
    except (TypeError, ValueError):
        return fallback


def reconcile_report_todos_with_trace(
    report_todos: list[DiagnosisTodo],
    trace: dict[str, Any],
) -> list[DiagnosisTodo]:
    """Merge report completion with the latest approval/action todo state."""

    trace_todos = [
        DiagnosisTodo.model_validate(todo)
        for todo in trace.get("todos") or []
    ]
    if not trace_todos:
        return report_todos

    report_by_id = {todo.todo_id: todo for todo in report_todos}
    merged: dict[str, DiagnosisTodo] = {}

    for trace_todo in trace_todos:
        report_todo = report_by_id.get(trace_todo.todo_id)
        if report_todo is not None and todo_group(report_todo) == TodoDisplayGroup.REPORT.value:
            merged[trace_todo.todo_id] = report_todo
        else:
            merged[trace_todo.todo_id] = trace_todo

    for report_todo in report_todos:
        merged.setdefault(report_todo.todo_id, report_todo)

    return sorted(
        merged.values(),
        key=lambda todo: (0 if str(todo.level) == "phase" else 1, todo.sort_order, todo.todo_index),
    )


def todo_group(todo: DiagnosisTodo) -> str:
    raw = getattr(todo.display_group, "value", todo.display_group)
    return str(raw)


def reconcile_report_text_with_trace(report_text: str, trace: dict[str, Any]) -> str:
    static_body = remove_dynamic_sections(report_text)
    sections = [
        static_body.rstrip(),
        build_latest_approval_section(trace),
        build_latest_risk_section(trace),
    ]
    return "\n\n".join(section for section in sections if section.strip()).strip()


def remove_dynamic_sections(report_text: str) -> str:
    lines = report_text.strip().splitlines()
    output: list[str] = []
    skip = False

    for line in lines:
        heading = h2_heading_title(line)
        if heading is not None:
            skip = heading in DYNAMIC_SECTION_TITLES
            if skip:
                continue

        if not skip:
            output.append(line)

    return "\n".join(output).rstrip()


def build_latest_approval_section(trace: dict[str, Any]) -> str:
    approvals = trace.get("approvals") or []
    action_results = trace.get("action_results") or []
    action_results_by_approval = group_action_results_by_approval(action_results)

    lines = ["## 审批状态"]
    if not approvals:
        lines.append("- none")
        return "\n".join(lines)

    for approval in approvals:
        approval_id = str(approval.get("approval_id") or "")
        action = str(approval.get("action") or "unknown")
        status = str(approval.get("status") or "unknown")
        risk = str(approval.get("risk") or "unknown")
        related_results = action_results_by_approval.get(approval_id, [])

        detail = latest_approval_detail(status=status, action_results=related_results)
        lines.append(
            f"- approval_id={approval_id} action={action} risk={risk} "
            f"status={status} {detail}".rstrip()
        )

    return "\n".join(lines)


def build_latest_risk_section(trace: dict[str, Any]) -> str:
    approvals = trace.get("approvals") or []
    action_results = trace.get("action_results") or []

    pending_count = sum(1 for approval in approvals if str(approval.get("status")) == "pending")
    rejected_count = sum(1 for approval in approvals if str(approval.get("status")) == "rejected")
    real_results = [result for result in action_results if str(result.get("mode")) == "real"]
    failed_or_blocked = [
        result for result in action_results
        if str(result.get("status")) not in {"success", "completed"}
    ]

    lines = ["## 风险说明"]
    if pending_count:
        lines.append(f"- 仍有 {pending_count} 个 pending approval；对应危险操作尚未执行。")
    elif real_results:
        previews = ", ".join(compact_preview(result) for result in real_results)
        lines.append(f"- 本次已有真实执行结果：{previews}。后续审计以 trace/action_results 为准。")
    elif action_results:
        previews = ", ".join(compact_preview(result) for result in action_results)
        lines.append(f"- 本次只有 dry-run/非真实执行结果：{previews}。dry-run 不改变系统状态。")
    elif rejected_count:
        lines.append(f"- {rejected_count} 个危险操作已被拒绝，未执行破坏性动作。")
    else:
        lines.append("- 未发现待审批或已执行的危险操作。")

    if failed_or_blocked:
        previews = ", ".join(compact_preview(result) for result in failed_or_blocked)
        lines.append(f"- 存在失败或阻塞的 action result：{previews}。")
    elif approvals:
        lines.append("- 危险操作状态来自最新 trace，不依赖 LLM 报告生成时的旧快照。")

    return "\n".join(lines)


def latest_approval_detail(*, status: str, action_results: list[dict[str, Any]]) -> str:
    if status == "pending":
        return "尚未执行。"
    if status == "rejected":
        return "用户已拒绝，危险操作未执行。"

    if not action_results:
        if status == "executed":
            return "审批已执行，但 trace 中没有 action_result。"
        return ""

    dry_run = latest_action_result(action_results, mode="dry_run")
    real = latest_action_result(action_results, mode="real")

    parts: list[str] = []
    if dry_run is not None:
        parts.append(f"dry_run={dry_run.get('status')} preview={dry_run.get('preview')}")
    if real is not None:
        parts.append(f"real={real.get('status')} preview={real.get('preview')}")
    if not parts:
        latest = action_results[-1]
        parts.append(f"{latest.get('mode')}={latest.get('status')} preview={latest.get('preview')}")

    return "；".join(parts) + "。"


def group_action_results_by_approval(
    action_results: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in action_results:
        approval_id = result.get("approval_id")
        if approval_id:
            grouped.setdefault(str(approval_id), []).append(result)
    return grouped


def latest_action_result(
    action_results: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any] | None:
    for result in reversed(action_results):
        if str(result.get("mode")) == mode:
            return result
    return None


def compact_preview(result: dict[str, Any]) -> str:
    action = result.get("action") or "unknown"
    mode = result.get("mode") or "unknown"
    status = result.get("status") or "unknown"
    preview = result.get("preview") or result.get("error") or ""
    if preview:
        return f"{action}/{mode}/{status} ({preview})"
    return f"{action}/{mode}/{status}"


def h2_heading_title(line: str) -> str | None:
    match = re.match(r"^##\s*(?P<title>.+?)\s*$", line.strip())
    if not match:
        return None
    return match.group("title").strip()
