import json

from app.cli import RichTodoEventSink, ask_approval_choice, main, run_interactive_approvals
from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from rich.console import Console
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


def test_json_only_cli(capsys) -> None:
    exit_code = main(["diagnose", "为什么 CPU 很高？", "--json-only"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "为什么 CPU 很高？" in captured.out


def test_trace_cli_prints_approvals(monkeypatch, capsys) -> None:
    class FakeTraceStore:
        def get_trace(self, run_id: str) -> dict:
            assert run_id == "run_test"
            return {
                "run": {
                    "run_id": "run_test",
                    "status": "waiting_approval",
                    "resource_type": "memory",
                    "user_input": "为什么内存快满了？",
                    "summary": "has one approval",
                },
                "steps": [
                    {
                        "step_index": 0,
                        "action": "infer_resource_type",
                        "observation_preview": "resource_type=memory",
                    }
                ],
                "findings": [
                    {
                        "finding_type": "memory_pressure",
                        "confidence": 0.9,
                    }
                ],
                "approvals": [
                    {
                        "approval_id": "appr_test",
                        "action": "kill_process",
                        "status": "pending",
                        "risk": "dangerous",
                    }
                ],
            }

    monkeypatch.setattr("app.cli.TraceStore", FakeTraceStore)

    exit_code = main(["trace", "run_test", "--full"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "approvals:" in captured.out
    assert "appr_test kill_process status=pending risk=dangerous" in captured.out


def test_cli_approve_syncs_trace(monkeypatch, tmp_path, capsys) -> None:
    trace_db = tmp_path / "resourceops.sqlite3"
    approval_path = tmp_path / "approvals.jsonl"
    workspace_root = tmp_path / "runs"
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(trace_db))
    monkeypatch.setenv("RESOURCEOPS_APPROVAL_STORE", str(approval_path))
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(workspace_root))

    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    trace_store.save_agent_result(result)
    from workspace.writer import WorkspaceWriter

    WorkspaceWriter().write_agent_result(result)

    approval_id = result.approvals[0]["approval_id"]

    exit_code = main(["approve", approval_id])
    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)

    assert exit_code == 0
    assert "已批准并模拟执行" in captured.out
    assert trace["run"]["status"] == "completed"
    assert trace["approvals"][0]["status"] == "executed"

    workspace_approvals = json.loads(
        (workspace_root / result.run.run_id / "trace" / "approvals.json").read_text(encoding="utf-8")
    )
    workspace_todos = json.loads(
        (workspace_root / result.run.run_id / "todos.json").read_text(encoding="utf-8")
    )
    approval_task = [todo for todo in workspace_todos if todo.get("approval_id") == approval_id][0]
    assert workspace_approvals[0]["status"] == "executed"
    assert approval_task["status"] == "completed"


def build_memory_approval_run(tmp_path):
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval_service = ApprovalService(store=approval_store)

    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(
        ResourceIncident(description="为什么内存快满了？", resource_type="memory")
    )
    trace_store.save_agent_result(result)
    return result, trace_store, approval_store


def test_interactive_approval_approve_syncs_trace(monkeypatch, tmp_path, capsys) -> None:
    result, trace_store, approval_store = build_memory_approval_run(tmp_path)
    approval_id = result.approvals[0]["approval_id"]

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    run_interactive_approvals(
        result.run.run_id,
        result.approvals,
        trace_store=trace_store,
        approval_store=approval_store,
    )

    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)
    approval_task = [todo for todo in trace["todos"] if todo.get("approval_id") == approval_id][0]

    assert "待审批操作" in captured.out
    assert "已批准并模拟执行" in captured.out
    assert "run_status=completed" in captured.out
    assert trace["run"]["status"] == "completed"
    assert trace["approvals"][0]["status"] == "executed"
    assert approval_task["status"] == "completed"


def test_interactive_approval_reject_syncs_trace(monkeypatch, tmp_path, capsys) -> None:
    result, trace_store, approval_store = build_memory_approval_run(tmp_path)
    approval_id = result.approvals[0]["approval_id"]

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    run_interactive_approvals(
        result.run.run_id,
        result.approvals,
        trace_store=trace_store,
        approval_store=approval_store,
    )

    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)
    approval_task = [todo for todo in trace["todos"] if todo.get("approval_id") == approval_id][0]

    assert "已拒绝审批" in captured.out
    assert "run_status=completed" in captured.out
    assert trace["run"]["status"] == "completed"
    assert trace["approvals"][0]["status"] == "rejected"
    assert approval_task["status"] == "skipped"


def test_interactive_approval_real_choice_records_blocked_real_result(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("RESOURCEOPS_ENABLE_REAL_ACTIONS", raising=False)
    monkeypatch.delenv("RESOURCEOPS_REAL_ACTION_ALLOWLIST", raising=False)
    result, trace_store, approval_store = build_memory_approval_run(tmp_path)
    approval_id = result.approvals[0]["approval_id"]

    monkeypatch.setattr("builtins.input", lambda _prompt: "r")
    run_interactive_approvals(
        result.run.run_id,
        result.approvals,
        trace_store=trace_store,
        approval_store=approval_store,
    )

    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)
    action_results = trace["action_results"]
    action_task = [
        todo for todo in trace["todos"]
        if todo.get("source") == "action_executor" and todo.get("approval_id") == approval_id
    ][0]

    assert "已批准并完成 dry-run" in captured.out
    assert "真实执行结果：blocked" in captured.out
    assert trace["run"]["status"] == "failed"
    assert trace["approvals"][0]["status"] == "executed"
    assert [item["mode"] for item in action_results] == ["dry_run", "real"]
    assert action_results[-1]["status"] == "blocked"
    assert "real execution is disabled" in action_results[-1]["error"]
    assert action_task["status"] == "failed"


def test_interactive_approval_refreshes_todo_sink_after_reject(monkeypatch, tmp_path) -> None:
    class FakeTodoSink:
        def __init__(self) -> None:
            self.snapshots = []

        def load_todos(self, todos, current_group=None) -> None:
            self.snapshots.append((todos, current_group))

    result, trace_store, approval_store = build_memory_approval_run(tmp_path)
    approval_id = result.approvals[0]["approval_id"]
    sink = FakeTodoSink()

    monkeypatch.setattr("builtins.input", lambda _prompt: "reject")
    run_interactive_approvals(
        result.run.run_id,
        result.approvals,
        trace_store=trace_store,
        approval_store=approval_store,
        event_sink=sink,
    )

    assert sink.snapshots
    assert sink.snapshots[-1][1] == "approval"

    final_todos = sink.snapshots[-1][0]
    approval_task = [todo for todo in final_todos if todo.approval_id == approval_id][0]
    approval_phase = [
        todo for todo in final_todos
        if todo.level == "phase" and todo.display_group == "approval"
    ][0]

    assert approval_task.status == "skipped"
    assert approval_task.result_preview == "rejected: kill_process"
    assert approval_phase.status == "completed"


def test_interactive_approval_skip_keeps_run_waiting(monkeypatch, tmp_path, capsys) -> None:
    result, trace_store, approval_store = build_memory_approval_run(tmp_path)

    monkeypatch.setattr("builtins.input", lambda _prompt: "s")
    run_interactive_approvals(
        result.run.run_id,
        result.approvals,
        trace_store=trace_store,
        approval_store=approval_store,
    )

    captured = capsys.readouterr()
    trace = trace_store.get_trace(result.run.run_id)

    assert "已跳过审批，保持 pending" in captured.out
    assert "pending_approvals=1" in captured.out
    assert "run_status=waiting_approval" in captured.out
    assert trace["run"]["status"] == "waiting_approval"
    assert trace["approvals"][0]["status"] == "pending"


def test_rich_todo_panel_keeps_tool_and_action_sections(tmp_path) -> None:
    result, trace_store, _approval_store = build_memory_approval_run(tmp_path)
    todos = trace_store.list_todos(result.run.run_id)

    sink = object.__new__(RichTodoEventSink)
    sink.phases = [todo for todo in todos if todo.level == "phase"]
    sink.todos = [todo for todo in todos if todo.level == "task"]
    sink.current_group_override = "approval"
    sink.live = None
    sink._closed = False
    sink._paused = False

    rendered = sink._render_tasks()
    console = Console(record=True, width=140)
    console.print(rendered)
    output = console.export_text()

    assert "Tool execution" in output
    assert "get_memory_snapshot" in output
    assert "Approval" in output
    assert "kill_process" in output
    assert "Action execution" in output
    assert "reserved for action executor" in output


def test_approval_prompt_pauses_live_without_printing_task_panel(monkeypatch) -> None:
    class FakeTodoSink:
        def __init__(self) -> None:
            self.pause_calls = []
            self.resume_calls = 0

        def pause(self, print_snapshot=False) -> None:
            self.pause_calls.append(print_snapshot)

        def resume(self) -> None:
            self.resume_calls += 1

    sink = FakeTodoSink()
    monkeypatch.setattr("builtins.input", lambda _prompt: "r")

    choice = ask_approval_choice(
        1,
        1,
        {
            "approval_id": "appr_test",
            "action": "kill_process",
            "risk": "dangerous",
            "reason": "test",
            "args": {"pid": 123},
        },
        event_sink=sink,
    )

    assert choice == "r"
    assert sink.pause_calls == [False]
    assert sink.resume_calls == 1


def test_workspace_cli_prints_file_list(monkeypatch, tmp_path, capsys) -> None:
    result, _trace_store, _approval_store = build_memory_approval_run(tmp_path)
    workspace_root = tmp_path / "runs"
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(workspace_root))

    from workspace.writer import WorkspaceWriter

    WorkspaceWriter().write_agent_result(result)

    exit_code = main(["workspace", result.run.run_id])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"workspace={workspace_root / result.run.run_id}" in captured.out
    assert "metadata.json" in captured.out
    assert "plan.json" in captured.out
    assert "compact/report_context.json" in captured.out
    assert "trace/approvals.json" in captured.out


def test_workspace_cli_json_output(monkeypatch, tmp_path, capsys) -> None:
    result, _trace_store, _approval_store = build_memory_approval_run(tmp_path)
    workspace_root = tmp_path / "runs"
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(workspace_root))

    from workspace.writer import WorkspaceWriter

    WorkspaceWriter().write_agent_result(result)

    exit_code = main(["workspace", result.run.run_id, "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["run_id"] == result.run.run_id
    assert payload["metadata"]["workspace_version"] == "p14"
    assert any(item["relative_path"] == "report.md" for item in payload["files"])


def test_workspace_cli_show_report_and_context(monkeypatch, tmp_path, capsys) -> None:
    result, _trace_store, _approval_store = build_memory_approval_run(tmp_path)
    workspace_root = tmp_path / "runs"
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(workspace_root))

    from workspace.writer import WorkspaceWriter

    WorkspaceWriter().write_agent_result(result)

    exit_code = main(["workspace", result.run.run_id, "--show-report"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Resource Diagnosis Report" in captured.out

    exit_code = main(["workspace", result.run.run_id, "--show-context"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["available"] is False


def test_bundle_cli_creates_debug_bundle(monkeypatch, tmp_path, capsys) -> None:
    result, _trace_store, _approval_store = build_memory_approval_run(tmp_path)
    workspace_root = tmp_path / "runs"
    monkeypatch.setenv("RESOURCEOPS_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("RESOURCEOPS_BUNDLE_ROOT", str(tmp_path / "bundles"))

    from workspace.writer import WorkspaceWriter

    WorkspaceWriter().write_agent_result(result)

    exit_code = main(["bundle", result.run.run_id, "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["run_id"] == result.run.run_id
    assert payload["bundle"].endswith(f"{result.run.run_id}.tar.gz")
