from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import ApprovalStatus, DiagnosisTodo, IncidentSource, ResourceIncident, ResourceType, Severity
from trace.store import TraceStore

from workspace.writer import WorkspaceWriter

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resourceops", description="ResourceOps Agent local CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose_parser = subparsers.add_parser("diagnose", help="Create a local resource diagnosis run.")
    diagnose_parser.add_argument("description", help="Resource problem description")
    diagnose_parser.add_argument(
        "--resource-type",
        choices=[item.value for item in ResourceType],
        default=None,
        help="Optional target resource scope.",
    )
    diagnose_parser.add_argument(
        "--severity",
        choices=[item.value for item in Severity],
        default=Severity.WARNING.value,
        help="Diagnosis severity.",
    )
    diagnose_parser.add_argument("--host", default=None, help="Optional host name.")
    diagnose_parser.add_argument(
        "--agent-mode",
        default=None,
        choices=["deterministic", "llm_report", "llm_planner", "llm_full"],
        help="Legacy combined mode. Prefer --planner-mode and --report-mode.",
    )
    diagnose_parser.add_argument(
        "--planner-mode",
        choices=["deterministic", "llm"],
        default=None,
        help="Tool planning mode.",
    )
    diagnose_parser.add_argument(
        "--report-mode",
        choices=["template", "llm"],
        default=None,
        help="Final report generation mode.",
    )
    diagnose_parser.add_argument("--json", action="store_true", help="Print structured JSON output.")
    diagnose_parser.add_argument(
        "--interactive-approval",
        action="store_true",
        help="Prompt to approve, reject, skip, or quit pending approvals after diagnosis.",
    )
    diagnose_parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only print the normalized ResourceIncident and skip diagnosis.",
    )

    runs_parser = subparsers.add_parser("runs", help="List recent diagnosis runs.")
    runs_parser.add_argument("--limit", type=int, default=20)
    runs_parser.add_argument("--json", action="store_true")

    trace_parser = subparsers.add_parser("trace", help="Show a traced diagnosis run.")
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--json", action="store_true")

    approvals_parser = subparsers.add_parser("approvals", help="List pending approvals.")
    approvals_parser.add_argument("--json", action="store_true")

    approve_parser = subparsers.add_parser("approve", help="Approve and simulate a dangerous action.")
    approve_parser.add_argument("approval_id")
    approve_parser.add_argument("--json", action="store_true")

    reject_parser = subparsers.add_parser("reject", help="Reject a pending dangerous action.")
    reject_parser.add_argument("approval_id")
    reject_parser.add_argument("--json", action="store_true")

    workspace_parser = subparsers.add_parser("workspace", help="Show files for a run workspace.")
    workspace_parser.add_argument("run_id")
    workspace_parser.add_argument("--json", action="store_true", help="Print workspace metadata and file list as JSON.")
    workspace_parser.add_argument("--show-report", action="store_true", help="Print report.md from the workspace.")
    workspace_parser.add_argument(
        "--show-context",
        action="store_true",
        help="Print compact/report_context.json from the workspace.",
    )

    bundle_parser = subparsers.add_parser("bundle", help="Create a debug bundle from a run workspace.")
    bundle_parser.add_argument("run_id")
    bundle_parser.add_argument("--json", action="store_true")

    return parser

def write_workspace_result(result) -> None:
    try:
        WorkspaceWriter().write_agent_result(result)
    except OSError as exc:
        print(f"workspace write failed: {exc}", file=sys.stderr)


def sync_workspace_from_trace(run_id: str, trace_store: TraceStore) -> None:
    try:
        WorkspaceWriter().update_from_trace(run_id, trace_store)
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"workspace sync failed: {exc}", file=sys.stderr)

def handle_diagnose(args: argparse.Namespace) -> int:
    incident = ResourceIncident(
        description=args.description,
        resource_type=args.resource_type,
        severity=args.severity,
        source=IncidentSource.CLI,
        host=args.host,
    )
    if args.json_only:
        print(json.dumps(incident.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    trace_store = TraceStore()
    event_sink = RichTodoEventSink() if not args.json else None
    keep_event_sink_for_approval = bool(event_sink is not None and args.interactive_approval)
    try:
        agent = ResourceAgent(
            approval_service=ApprovalService(),
            agent_mode=args.agent_mode,
            planner_mode=args.planner_mode,
            report_mode=args.report_mode,
            event_sink=event_sink,
        )
        result = agent.diagnose(incident)
    except Exception:
        if event_sink is not None:
            event_sink.close()
        raise
    finally:
        if event_sink is not None and not keep_event_sink_for_approval:
            event_sink.close()
   
    trace_store.save_agent_result(result)
    write_workspace_result(result)

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print_diagnosis_report(result.final_report, result.run.run_id, event_sink if args.interactive_approval else None)
        if args.interactive_approval:
            try:
                if event_sink is not None:
                    refresh_todo_sink_from_trace(event_sink, result.run.run_id, trace_store)
                run_interactive_approvals(
                    result.run.run_id,
                    result.approvals,
                    trace_store=trace_store,
                    event_sink=event_sink,
                )
            finally:
                if event_sink is not None:
                    event_sink.close()
    return 0


def run_interactive_approvals(
    run_id: str,
    approvals: list[dict[str, Any]],
    trace_store: TraceStore | None = None,
    approval_store: ApprovalStore | None = None,
    event_sink: "RichTodoEventSink | None" = None,
) -> None:
    """在 CLI diagnose 结束后可选地处理本次 run 的 pending approvals。

    API 和默认 CLI 诊断仍然是非阻塞的；只有显式传入 --interactive-approval
    时才会调用这个函数。
    """

    trace_store = trace_store or TraceStore()
    approval_store = approval_store or ApprovalStore()
    approval_service = ApprovalService(store=approval_store)
    pending = pending_approval_dicts(run_id, approvals, approval_store)

    if not pending:
        print("\n当前没有 pending approval。")
        refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
        print_interactive_run_status(run_id, trace_store, event_sink=event_sink)
        return

    refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
    print_pending_approval_list(run_id, pending, event_sink)

    decisions: list[tuple[str, str]] = []
    for index, approval in enumerate(pending, 1):
        approval_id = str(approval["approval_id"])

        while True:
            choice = ask_approval_choice(index, len(pending), approval, event_sink)
            if choice in {"y", "yes", "a", "approve"}:
                try:
                    approved, tool_result = approval_service.approve(approval_id)
                except (KeyError, ValueError) as error:
                    decisions.append(("error", approval_id))
                    print_interactive_lines(event_sink, f"审批处理失败：{approval_id} error={error}")
                else:
                    sync_approval_trace(trace_store, approval_store, approved)
                    sync_workspace_from_trace(run_id, trace_store)
                    refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
                    decisions.append(("approved", approval_id))
                    print_interactive_lines(
                        event_sink,
                        f"已批准并模拟执行：{approved.action} {approved.args}",
                        f"tool_result={tool_result.status} preview={tool_result.preview}",
                    )
                break

            if choice in {"n", "no", "r", "reject"}:
                try:
                    rejected = approval_service.reject(approval_id)
                except (KeyError, ValueError) as error:
                    decisions.append(("error", approval_id))
                    print_interactive_lines(event_sink, f"审批处理失败：{approval_id} error={error}")
                else:
                    sync_approval_trace(trace_store, approval_store, rejected)
                    sync_workspace_from_trace(run_id, trace_store)
                    refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
                    decisions.append(("rejected", approval_id))
                    print_interactive_lines(event_sink, f"已拒绝审批：{rejected.approval_id}")
                break

            if choice in {"s", "skip", ""}:
                refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
                decisions.append(("skipped", approval_id))
                print_interactive_lines(event_sink, f"已跳过审批，保持 pending：{approval_id}")
                break

            if choice in {"q", "quit", "exit"}:
                refresh_todo_sink_from_trace(event_sink, run_id, trace_store)
                decisions.append(("quit", approval_id))
                print_interactive_lines(event_sink, "已退出交互审批，剩余 pending approval 保持不变。")
                print_interactive_summary(run_id, decisions, trace_store, approval_store, event_sink)
                return

    print_interactive_summary(run_id, decisions, trace_store, approval_store, event_sink)


def refresh_todo_sink_from_trace(
    event_sink: "RichTodoEventSink | None",
    run_id: str,
    trace_store: TraceStore,
) -> None:
    if event_sink is None:
        return

    try:
        todos = trace_store.list_todos(run_id)
    except KeyError:
        return

    event_sink.load_todos(todos, current_group="approval")


def pending_approval_dicts(
    run_id: str,
    approvals: list[dict[str, Any]],
    approval_store: ApprovalStore,
) -> list[dict[str, Any]]:
    stored = {
        approval.approval_id: approval
        for approval in approval_store.list(status=None)
        if approval.run_id == run_id
    }
    ordered_ids = [str(approval["approval_id"]) for approval in approvals if approval.get("approval_id")]
    ordered_ids.extend(approval_id for approval_id in stored if approval_id not in ordered_ids)

    pending: list[dict[str, Any]] = []
    for approval_id in ordered_ids:
        approval = stored.get(approval_id)
        if approval is not None:
            if normalize_value(approval.status) == ApprovalStatus.PENDING.value:
                pending.append(approval.model_dump(mode="json"))
            continue

        snapshot = next(
            (item for item in approvals if item.get("approval_id") == approval_id),
            None,
        )
        if snapshot and normalize_value(snapshot.get("status")) == ApprovalStatus.PENDING.value:
            pending.append(snapshot)

    return pending


def print_approval_summary(
    index: int,
    total: int,
    approval: dict[str, Any],
    console: Console | None = None,
) -> None:
    console = console or Console()
    console.print(
        f"[yellow]- [{index}/{total}] {approval['approval_id']}[/] "
        f"action=[bold]{approval['action']}[/] "
        f"risk=[bold {risk_style(approval.get('risk'))}]{approval['risk']}[/] "
        f"status=[bold {approval_status_style(approval.get('status'))}]{approval['status']}[/]"
    )


def print_approval_prompt(
    index: int,
    total: int,
    approval: dict[str, Any],
    console: Console | None = None,
) -> None:
    console = console or Console()
    console.print(f"\n[bold yellow]审批 [{index}/{total}][/]")
    console.print(f"[yellow]approval_id[/]={approval['approval_id']}")
    console.print(f"[yellow]action[/]=[bold]{approval['action']}[/]")
    console.print(f"[yellow]risk[/]=[bold {risk_style(approval.get('risk'))}]{approval['risk']}[/]")
    console.print(f"[yellow]reason[/]={approval['reason']}")
    console.print(f"[yellow]args[/]={json.dumps(approval.get('args') or {}, ensure_ascii=False)}")


def print_diagnosis_report(
    final_report: str,
    run_id: str,
    event_sink: "RichTodoEventSink | None" = None,
) -> None:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        print(final_report)
        print(f"\nrun_id={run_id}")
    finally:
        resume_event_sink(event_sink)


def print_pending_approval_list(
    run_id: str,
    pending: list[dict[str, Any]],
    event_sink: "RichTodoEventSink | None" = None,
) -> None:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        console = event_console(event_sink)
        console.print(f"\n[bold yellow]待审批操作[/] run_id={run_id} count=[bold yellow]{len(pending)}[/]")
        for index, approval in enumerate(pending, 1):
            print_approval_summary(index, len(pending), approval, console=console)
    finally:
        resume_event_sink(event_sink)


def ask_approval_choice(
    index: int,
    total: int,
    approval: dict[str, Any],
    event_sink: "RichTodoEventSink | None" = None,
) -> str:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        console = event_console(event_sink)
        print_approval_prompt(index, total, approval, console=console)
        while True:
            console.print(
                "[bold yellow]选择：[/]"
                "[green]y=批准[/] / "
                "[red]n=拒绝[/] / "
                "[cyan]s=跳过[/] / "
                "[dim]q=退出[/] > ",
                end="",
            )
            choice = input("").strip().lower()
            if choice in {
                "y",
                "yes",
                "a",
                "approve",
                "n",
                "no",
                "r",
                "reject",
                "s",
                "skip",
                "",
                "q",
                "quit",
                "exit",
            }:
                return choice
            print("输入无效，请输入 y / n / s / q。")
    finally:
        resume_event_sink(event_sink)


def print_interactive_summary(
    run_id: str,
    decisions: list[tuple[str, str]],
    trace_store: TraceStore,
    approval_store: ApprovalStore,
    event_sink: "RichTodoEventSink | None" = None,
) -> None:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        console = event_console(event_sink)
        if decisions:
            console.print("\n[bold]审批结果：[/]")
            for decision, approval_id in decisions:
                console.print(f"- {approval_id}: [{decision_style(decision)}]{decision}[/]")
        else:
            console.print("\n[dim]没有处理任何审批。[/]")

        pending_count = sum(
            1
            for approval in approval_store.list(status=None)
            if approval.run_id == run_id and normalize_value(approval.status) == ApprovalStatus.PENDING.value
        )
        pending_style = "yellow" if pending_count else "green"
        console.print(f"pending_approvals=[bold {pending_style}]{pending_count}[/]")
        print_interactive_run_status(run_id, trace_store)
    finally:
        resume_event_sink(event_sink)


def print_interactive_run_status(
    run_id: str,
    trace_store: TraceStore,
    event_sink: "RichTodoEventSink | None" = None,
) -> None:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        console = event_console(event_sink)
        try:
            trace = trace_store.get_trace(run_id)
        except KeyError:
            console.print("run_status=[red]unknown[/]")
            return
        run_status = trace["run"]["status"]
        console.print(f"run_status=[bold {run_status_style(run_status)}]{run_status}[/]")
    finally:
        resume_event_sink(event_sink)


def print_interactive_lines(
    event_sink: "RichTodoEventSink | None",
    *lines: str,
) -> None:
    pause_event_sink(event_sink, print_snapshot=False)
    try:
        console = event_console(event_sink)
        for line in lines:
            console.print(color_interactive_line(line))
    finally:
        resume_event_sink(event_sink)


def event_console(event_sink: object | None) -> Console:
    console = getattr(event_sink, "console", None)
    if isinstance(console, Console):
        return console
    return Console()


def risk_style(risk: object) -> str:
    value = normalize_value(risk)
    if value == "dangerous":
        return "red"
    if value == "write":
        return "yellow"
    return "green"


def approval_status_style(status: object) -> str:
    value = normalize_value(status)
    if value == "pending":
        return "yellow"
    if value in {"executed", "approved"}:
        return "green"
    if value in {"rejected", "cancelled"}:
        return "red"
    return "dim"


def decision_style(decision: str) -> str:
    if decision == "approved":
        return "green"
    if decision == "rejected":
        return "red"
    if decision in {"skipped", "quit"}:
        return "yellow"
    return "red"


def run_status_style(status: object) -> str:
    value = normalize_value(status)
    if value == "completed":
        return "green"
    if value == "waiting_approval":
        return "yellow"
    if value == "failed":
        return "red"
    return "cyan"


def color_interactive_line(line: str) -> str:
    if line.startswith("已批准"):
        return f"[green]{line}[/]"
    if line.startswith("tool_result=success"):
        return f"[green]{line}[/]"
    if line.startswith("已拒绝") or line.startswith("审批处理失败"):
        return f"[red]{line}[/]"
    if line.startswith("已跳过") or line.startswith("已退出"):
        return f"[yellow]{line}[/]"
    return line


def pause_event_sink(event_sink: object | None, print_snapshot: bool = False) -> None:
    pause = getattr(event_sink, "pause", None)
    if callable(pause):
        pause(print_snapshot=print_snapshot)


def resume_event_sink(event_sink: object | None) -> None:
    resume = getattr(event_sink, "resume", None)
    if callable(resume):
        resume()


def handle_runs(args: argparse.Namespace) -> int:
    runs = TraceStore().list_runs(limit=args.limit)
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2))
    elif runs:
        for run in runs:
            print(f"{run['run_id']} {run['status']} {run['resource_type']} {run['user_input']}")
    else:
        print("当前没有 diagnosis runs。")
    return 0


def handle_workspace(args: argparse.Namespace) -> int:
    run_dir = WorkspaceWriter().run_dir(args.run_id)
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"workspace not found: {run_dir}", file=sys.stderr)
        return 1

    payload = workspace_payload(run_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.show_report:
        print_workspace_file(run_dir / "report.md")
        return 0

    if args.show_context:
        print_workspace_json_file(run_dir / "compact" / "report_context.json")
        return 0

    print(f"workspace={run_dir}")
    metadata = payload.get("metadata") or {}
    if metadata:
        print(
            f"run_id={metadata.get('run_id')} "
            f"status={metadata.get('status')} "
            f"resource_type={metadata.get('resource_type')} "
            f"workspace_version={metadata.get('workspace_version')}"
        )
    print("\nfiles:")
    for item in payload["files"]:
        print(f"- {item['relative_path']}")
    return 0


def handle_bundle(args: argparse.Namespace) -> int:
    try:
        bundle_path = WorkspaceWriter().create_bundle(args.run_id)
    except OSError as exc:
        print(f"bundle failed: {exc}", file=sys.stderr)
        return 1

    payload = {
        "run_id": args.run_id,
        "bundle": str(bundle_path),
        "size_bytes": bundle_path.stat().st_size,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"bundle={bundle_path}")
    return 0


def workspace_payload(run_dir: Path) -> dict[str, Any]:
    files = []
    for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
        files.append(
            {
                "relative_path": path.relative_to(run_dir).as_posix(),
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
        )

    return {
        "workspace": str(run_dir),
        "run_id": run_dir.name,
        "metadata": read_workspace_json(run_dir / "metadata.json"),
        "files": files,
    }


def read_workspace_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def print_workspace_file(path: Path) -> None:
    if not path.exists():
        print(f"workspace file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    print(path.read_text(encoding="utf-8").rstrip())


def print_workspace_json_file(path: Path) -> None:
    payload = read_workspace_json(path)
    if payload is None:
        print(f"workspace file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


class RichTodoEventSink:
    def __init__(self) -> None:
        self.console = Console()
        self.phases: list[DiagnosisTodo] = []
        self.todos: list[DiagnosisTodo] = []
        self.current_group_override: str | None = None
        self.live: Live | None = None
        self._closed = False
        self._paused = False
        self._start_live()

    def _start_live(self) -> None:
        self.live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=8,
            transient=True,
            screen=True,
        )
        self.live.start()

    def close(self) -> None:
        if not self._closed:
            if self.live is not None:
                self.live.update(self.render())
                self.live.stop()
            self.console.print(self.render())
            self._closed = True
            self._paused = False

    def pause(self, print_snapshot: bool = False) -> None:
        if self._closed or self._paused:
            return
        if self.live is not None:
            self.live.update(self.render())
            self.live.stop()
            self.live = None
        if print_snapshot:
            self.console.print(self.render())
        self._paused = True

    def resume(self) -> None:
        if self._closed or not self._paused:
            return
        self._paused = False
        self._start_live()

    def _update_live(self) -> None:
        if self.live is not None and not self._paused and not self._closed:
            self.live.update(self.render())

    def on_phase_snapshot(self, phases: list[DiagnosisTodo]) -> None:
        self.current_group_override = None
        self.phases = phases
        self._update_live()

    def on_phase_updated(self, phase: DiagnosisTodo, phases: list[DiagnosisTodo]) -> None:
        self.current_group_override = None
        self.phases = phases
        self._update_live()

    def on_todo_snapshot(self, todos: list[DiagnosisTodo]) -> None:
        self.current_group_override = None
        self.todos = todos
        self._update_live()

    def on_todo_updated(self, todo: DiagnosisTodo, todos: list[DiagnosisTodo]) -> None:
        self.current_group_override = None
        self.todos = todos
        self._update_live()

    def load_todos(self, todos: list[DiagnosisTodo], current_group: str | None = None) -> None:
        self.current_group_override = current_group
        self.phases = [todo for todo in todos if normalize_value(todo.level) == "phase"]
        self.todos = [todo for todo in todos if normalize_value(todo.level) == "task"]
        self._update_live()

    def render(self) -> Group:
        return Group(
            Panel(self._render_phases(), title="[bold bright_blue]ResourceOps Agent[/]", border_style="bright_blue"),
            Panel(self._render_tasks(), title="[blue]Current tasks[/]", border_style="blue"),
        )

    def _render_phases(self) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column("status", no_wrap=True)
        table.add_column("phase")
        table.add_column("detail")

        if not self.phases:
            table.add_row("[cyan]...[/]", "[bold bright_blue]Preparing run[/]", "")
            return table

        for phase in sorted(self.phases, key=lambda item: item.sort_order):
            status = normalize_status(phase.status)
            table.add_row(
                status_icon(status),
                Text(str(phase.title), style=phase_title_style(status)),
                Text(phase.result_preview or phase.error or "", style=status_style(status)),
            )
        return table

    def _render_tasks(self) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column("status", no_wrap=True)
        table.add_column("task")
        table.add_column("detail")

        tasks = [todo for todo in self.todos if normalize_value(todo.level) == "task"]
        groups = [
            ("tools", "Tool execution", True),
            ("approval", "Approval", False),
            ("actions", "Action execution", True),
        ]
        added_rows = False

        for group, title, always_show in groups:
            group_tasks = sorted(
                [todo for todo in tasks if normalize_value(todo.display_group) == group],
                key=lambda item: item.sort_order,
            )

            if not group_tasks and not always_show:
                continue

            phase = self._phase_for_group(group)
            phase_status = normalize_status(phase.status) if phase is not None else "pending"
            phase_detail = ""
            if phase is not None:
                phase_detail = phase.result_preview or phase.error or ""

            table.add_row(
                status_icon(phase_status),
                Text(title, style=group_title_style(group, phase_status)),
                Text(phase_detail, style=status_style(phase_status)),
            )
            added_rows = True

            if not group_tasks:
                if group != "actions":
                    table.add_row("[dim]-[/]", Text("  No task details", style="dim"), "")
                continue

            for todo in group_tasks:
                status = normalize_status(todo.status)
                table.add_row(
                    status_icon(status),
                    Text(f"  {todo.tool_name or todo.title}", style=task_title_style(status)),
                    Text(todo.result_preview or todo.error or "", style=status_style(status)),
                )

        if not added_rows:
            table.add_row("[dim]-[/]", Text("No current task details", style="dim"), "")
        return table

    def _phase_for_group(self, group: str) -> DiagnosisTodo | None:
        for phase in self.phases:
            if normalize_value(phase.display_group) == group:
                return phase
        return None


def normalize_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw) if raw is not None else ""


def normalize_status(status: object) -> str:
    return normalize_value(status)


def status_icon(status: object) -> str:
    status = normalize_status(status)
    if status == "completed":
        return "[green]✓[/]"
    if status == "running":
        return "[cyan]●[/]"
    if status == "failed":
        return "[red]×[/]"
    if status == "waiting_approval":
        return "[yellow]![/]"
    if status == "skipped":
        return "[dim]-[/]"
    return "[dim]○[/]"


def status_style(status: object) -> str:
    status = normalize_status(status)
    if status == "completed":
        return "green"
    if status == "running":
        return "cyan"
    if status == "failed":
        return "red"
    if status == "waiting_approval":
        return "yellow"
    return "dim"


def phase_title_style(status: object) -> str:
    status = normalize_status(status)
    base = "bold bright_blue"
    if status == "completed":
        return f"{base} green"
    if status == "failed":
        return "bold red"
    if status == "waiting_approval":
        return "bold yellow"
    if status == "skipped":
        return "dim"
    return base


def task_title_style(status: object) -> str:
    status = normalize_status(status)
    if status == "completed":
        return "green"
    if status == "running":
        return "blue"
    if status == "failed":
        return "red"
    if status == "waiting_approval":
        return "yellow"
    return "dim blue"


def group_title_style(group: str, status: object) -> str:
    status = normalize_status(status)
    if status == "completed":
        return "bold green"
    if status == "running":
        return "bold blue"
    if status == "failed":
        return "bold red"
    if status == "waiting_approval":
        return "bold yellow"
    if group == "actions":
        return "dim blue"
    return "bold blue"

def format_todo_panel(todos: list[dict]) -> str:
    if not todos:
        return ""

    phases = sorted(
        [todo for todo in todos if todo.get("level") == "phase"],
        key=lambda item: item.get("sort_order", 0),
    )
    tasks = sorted(
        [todo for todo in todos if todo.get("level") == "task"],
        key=lambda item: item.get("sort_order", 0),
    )

    tasks_by_parent: dict[str, list[dict]] = {}
    orphan_tasks: list[dict] = []
    for task in tasks:
        parent_id = task.get("parent_todo_id")
        if parent_id:
            tasks_by_parent.setdefault(parent_id, []).append(task)
        else:
            orphan_tasks.append(task)

    lines = ["\ntodos:"]
    for phase in phases:
        lines.append(_format_todo_line(phase, indent="  "))
        for task in tasks_by_parent.get(phase.get("todo_id"), []):
            lines.append(_format_todo_line(task, indent="    "))

    for task in orphan_tasks:
        lines.append(_format_todo_line(task, indent="    "))

    return "\n".join(lines)


def _format_todo_line(todo: dict, indent: str) -> str:
    icon = plain_todo_status_icon(todo.get("status"))
    label = todo.get("tool_name") or todo.get("title") or "unknown"
    detail = todo.get("result_preview") or todo.get("error") or ""
    if todo.get("approval_id"):
        detail = f"{detail} approval_id={todo['approval_id']}".strip()
    if detail:
        detail = f"  {detail}"
    return f"{indent}{icon} {label}{detail}"


def plain_todo_status_icon(status: str | None) -> str:
    status = normalize_status(status)
    if status == "completed":
        return "[√]"
    if status == "running":
        return "[...]"
    if status == "failed":
        return "[×]"
    if status == "waiting_approval":
        return "[!]"
    if status == "skipped":
        return "[-]"
    return "[ ]"


def handle_trace(args: argparse.Namespace) -> int:
    trace = TraceStore().get_trace(args.run_id)
    if args.json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        run = trace["run"]
        print(f"run_id={run['run_id']} status={run['status']} resource_type={run['resource_type']}")
        print(f"user_input={run['user_input']}")
        print(f"summary={run['summary']}")
        plan_steps = []
        for step in trace["steps"]:
            if step["action"] != "build_tool_plan":
                continue
            observation = step.get("observation") or {}
            tool_plan = observation.get("tool_plan") or {}
            plan_steps = tool_plan.get("steps") or []
            if tool_plan:
                print(
                    f"tool_plan={tool_plan.get('planner_mode')} "
                    f"steps={len(plan_steps)} plan_id={tool_plan.get('plan_id')}"
                )
            break
        print("\nsteps:")
        for step in trace["steps"]:
            print(f"- #{step['step_index']} {step['action']} preview={step['observation_preview']}")
        if plan_steps:
            print("\nplanned tools:")
            for planned in plan_steps:
                print(
                    f"- #{planned['step_index']} {planned['tool_name']} "
                    f"risk={planned['permission_level']} approval={planned['requires_approval']} "
                    f"args={planned['args']}"
                )
        todo_panel = format_todo_panel(trace.get("todos") or [])
        if todo_panel:
            print(todo_panel)
            
        print("\nfindings:")
        if trace["findings"]:
            for finding in trace["findings"]:
                print(f"- {finding['finding_type']} confidence={finding['confidence']}")
        else:
            print("- none")
        print("\napprovals:")
        if trace["approvals"]:
            for approval in trace["approvals"]:
                print(
                    f"- {approval['approval_id']} {approval['action']} "
                    f"status={approval['status']} risk={approval['risk']}"
                )
        else:
            print("- none")
    return 0


def handle_approvals(args: argparse.Namespace) -> int:
    approvals = [approval.model_dump(mode="json") for approval in ApprovalStore().list(status="pending")]
    if args.json:
        print(json.dumps(approvals, ensure_ascii=False, indent=2))
    elif approvals:
        for approval in approvals:
            print(f"{approval['approval_id']} {approval['action']} {approval['args']} reason={approval['reason']}")
    else:
        print("当前没有 pending approval。")
    return 0


def handle_approve(args: argparse.Namespace) -> int:
    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval, tool_result = ApprovalService(store=approval_store).approve(args.approval_id)
    sync_approval_trace(trace_store, approval_store, approval)
    sync_workspace_from_trace(approval.run_id, trace_store)
    payload = {"approval": approval.model_dump(mode="json"), "tool_result": tool_result.model_dump(mode="json")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"已批准并模拟执行：{approval.action} {approval.args}")
    return 0


def handle_reject(args: argparse.Namespace) -> int:
    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval = ApprovalService(store=approval_store).reject(args.approval_id)
    sync_approval_trace(trace_store, approval_store, approval)
    sync_workspace_from_trace(approval.run_id, trace_store)
    if args.json:
        print(json.dumps(approval.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"已拒绝审批：{approval.approval_id} status={approval.status}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "diagnose":
        return handle_diagnose(args)
    if args.command == "runs":
        return handle_runs(args)
    if args.command == "trace":
        return handle_trace(args)
    if args.command == "approvals":
        return handle_approvals(args)
    if args.command == "approve":
        return handle_approve(args)
    if args.command == "reject":
        return handle_reject(args)
    if args.command == "workspace":
        return handle_workspace(args)
    if args.command == "bundle":
        return handle_bundle(args)
    parser.error(f"unknown command: {args.command}")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
