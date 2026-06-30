import os
import subprocess
import sys

from actions.executor import ActionExecutor, ActionMode, ActionStatus
from app.schemas import Approval, ApprovalStatus, RiskLevel, ToolPermissionLevel
from approval.service import ApprovalService
from approval.store import ApprovalStore


def make_executed_approval(pid: int = 4321) -> Approval:
    return Approval(
        run_id="run_test",
        action="kill_process",
        args={"pid": pid, "command_preview": f"kill {pid}"},
        reason="test approval",
        risk=RiskLevel.DANGEROUS,
        status=ApprovalStatus.EXECUTED,
    )


def make_renice_approval(pid: int = 4321, nice: int = 5) -> Approval:
    return Approval(
        run_id="run_test",
        action="renice_process",
        args={"pid": pid, "nice": nice, "command_preview": f"renice -n {nice} -p {pid}"},
        reason="test renice approval",
        risk=RiskLevel.WRITE,
        status=ApprovalStatus.EXECUTED,
    )


class FakeProcess:
    def __init__(self, pid: int, *, username: str = "zcj", name: str = "python", nice: int = 0) -> None:
        self.pid = pid
        self._username = username
        self._name = name
        self._nice = nice
        self.terminated = False

    def username(self) -> str:
        return self._username

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return ["python", "worker.py"]

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> int:
        return 0

    def nice(self, value: int | None = None) -> int | None:
        if value is None:
            return self._nice
        self._nice = value
        return None


def test_p13_real_execution_disabled_by_default() -> None:
    approval = make_executed_approval()

    result = ActionExecutor().execute(
        "kill_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.BLOCKED
    assert "real execution is disabled" in result.error
    assert result.execution["changed_system_state"] is False


def test_p13_real_execution_requires_allowlist() -> None:
    approval = make_executed_approval()
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist=set())

    result = executor.execute(
        "kill_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "real action is not allowlisted: kill_process"


def test_p13_dangerous_real_execution_requires_confirmation() -> None:
    approval = make_executed_approval()
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"})

    result = executor.execute("kill_process", approval.args, mode=ActionMode.REAL, approval=approval)

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "dangerous real action requires confirm_real=True"


def test_p13_real_kill_blocks_current_process() -> None:
    approval = make_executed_approval(os.getpid())
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"})

    result = executor.execute(
        "kill_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.BLOCKED
    assert "protected pid" in result.error
    assert result.execution["changed_system_state"] is False


def test_p13_real_kill_blocks_root_owned_process(monkeypatch) -> None:
    fake = FakeProcess(4321, username="root", name="python")
    monkeypatch.setattr("actions.executor.psutil.Process", lambda pid: fake)

    approval = make_executed_approval(4321)
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"})

    result = executor.execute(
        "kill_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.BLOCKED
    assert "refusing protected process" in result.error
    assert result.pre_check["passed"] is False


def test_p13_real_kill_success_with_fake_process(monkeypatch) -> None:
    fake = FakeProcess(4321)
    monkeypatch.setattr("actions.executor.psutil.Process", lambda pid: fake)
    monkeypatch.setattr("actions.executor.psutil.pid_exists", lambda pid: not fake.terminated)

    approval = make_executed_approval(4321)
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"})

    result = executor.execute(
        "kill_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.SUCCESS
    assert result.mode == ActionMode.REAL
    assert result.pre_check["passed"] is True
    assert "dry_run_preview" in result.pre_check
    assert result.execution["simulated"] is False
    assert result.execution["changed_system_state"] is True
    assert result.post_check["passed"] is True
    assert result.post_check["process_exists_after"] is False


def test_p13_approval_service_real_execution_returns_non_simulated_tool_result(monkeypatch, tmp_path) -> None:
    fake = FakeProcess(4321)
    monkeypatch.setattr("actions.executor.psutil.Process", lambda pid: fake)
    monkeypatch.setattr("actions.executor.psutil.pid_exists", lambda pid: not fake.terminated)

    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval = approval_store.save(make_executed_approval(4321))
    service = ApprovalService(
        store=approval_store,
        action_executor=ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"}),
    )

    updated, tool_result, action_result = service.execute_real_approved_action(
        approval.approval_id,
        confirm_real=True,
    )

    assert updated.approval_id == approval.approval_id
    assert action_result.status == ActionStatus.SUCCESS
    assert action_result.mode == ActionMode.REAL
    assert tool_result.status == "success"
    assert tool_result.data["simulated"] is False
    assert tool_result.data["action_result"]["mode"] == ActionMode.REAL


def test_p13_real_kill_live_smoke_terminates_owned_child_process(tmp_path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
        approval = approval_store.save(make_executed_approval(process.pid))
        service = ApprovalService(
            store=approval_store,
            action_executor=ActionExecutor(real_actions_enabled=True, real_action_allowlist={"kill_process"}),
        )

        _updated, tool_result, action_result = service.execute_real_approved_action(
            approval.approval_id,
            confirm_real=True,
        )

        assert action_result.status == ActionStatus.SUCCESS
        assert action_result.mode == ActionMode.REAL
        assert action_result.execution["simulated"] is False
        assert action_result.execution["changed_system_state"] is True
        assert action_result.post_check["process_exists_after"] is False
        assert tool_result.data["simulated"] is False
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_p13_renice_process_dry_run_does_not_change_state() -> None:
    approval = make_renice_approval()

    result = ActionExecutor().execute("renice_process", approval.args, approval=approval)

    assert result.action == "renice_process"
    assert result.mode == ActionMode.DRY_RUN
    assert result.status == ActionStatus.SUCCESS
    assert result.execution["simulated"] is True
    assert result.execution["changed_system_state"] is False
    assert result.execution["would_execute"] == "renice -n 5 -p 4321"


def test_p13_renice_process_rejects_invalid_nice() -> None:
    approval = make_renice_approval(nice=30)

    result = ActionExecutor().execute("renice_process", approval.args, approval=approval)

    assert result.status == ActionStatus.FAILED
    assert result.error == "nice must be an integer between -20 and 19"


def test_p13_renice_real_execution_requires_confirmation() -> None:
    approval = make_renice_approval()
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"renice_process"})

    result = executor.execute("renice_process", approval.args, mode=ActionMode.REAL, approval=approval)

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "real action requires confirm_real=True"


def test_p13_real_renice_success_with_fake_process(monkeypatch) -> None:
    fake = FakeProcess(4321, nice=0)
    monkeypatch.setattr("actions.executor.psutil.Process", lambda pid: fake)

    approval = make_renice_approval(4321, nice=5)
    executor = ActionExecutor(real_actions_enabled=True, real_action_allowlist={"renice_process"})

    result = executor.execute(
        "renice_process",
        approval.args,
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.SUCCESS
    assert result.mode == ActionMode.REAL
    assert result.execution["simulated"] is False
    assert result.execution["changed_system_state"] is True
    assert result.execution["before_nice"] == 0
    assert result.execution["after_nice"] == 5
    assert result.post_check["actual_nice"] == 5


def test_p13_approval_service_renice_real_execution_uses_write_permission(monkeypatch, tmp_path) -> None:
    fake = FakeProcess(4321, nice=0)
    monkeypatch.setattr("actions.executor.psutil.Process", lambda pid: fake)

    approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
    approval = approval_store.save(make_renice_approval(4321, nice=5))
    service = ApprovalService(
        store=approval_store,
        action_executor=ActionExecutor(real_actions_enabled=True, real_action_allowlist={"renice_process"}),
    )

    _updated, tool_result, action_result = service.execute_real_approved_action(
        approval.approval_id,
        confirm_real=True,
    )

    assert action_result.status == ActionStatus.SUCCESS
    assert action_result.mode == ActionMode.REAL
    assert tool_result.permission_level == ToolPermissionLevel.WRITE
    assert tool_result.data["simulated"] is False


def test_p13_real_renice_live_smoke_changes_owned_child_process_nice(tmp_path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        approval_store = ApprovalStore(tmp_path / "approvals.jsonl")
        approval = approval_store.save(make_renice_approval(process.pid, nice=5))
        service = ApprovalService(
            store=approval_store,
            action_executor=ActionExecutor(real_actions_enabled=True, real_action_allowlist={"renice_process"}),
        )

        _updated, tool_result, action_result = service.execute_real_approved_action(
            approval.approval_id,
            confirm_real=True,
        )

        assert action_result.status == ActionStatus.SUCCESS
        assert action_result.mode == ActionMode.REAL
        assert action_result.execution["simulated"] is False
        assert action_result.execution["requested_nice"] == 5
        assert action_result.post_check["actual_nice"] == 5
        assert tool_result.permission_level == ToolPermissionLevel.WRITE
        assert tool_result.data["simulated"] is False
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_p13_inspect_process_dry_run_without_approval() -> None:
    result = ActionExecutor().execute("inspect_process", {"pid": os.getpid()})

    assert result.action == "inspect_process"
    assert result.mode == ActionMode.DRY_RUN
    assert result.status == ActionStatus.SUCCESS
    assert result.approval_id is None
    assert result.execution["simulated"] is True
    assert result.execution["changed_system_state"] is False


def test_p13_inspect_process_real_is_safe_without_env_allowlist_or_approval(monkeypatch) -> None:
    monkeypatch.delenv("RESOURCEOPS_ENABLE_REAL_ACTIONS", raising=False)
    monkeypatch.delenv("RESOURCEOPS_REAL_ACTION_ALLOWLIST", raising=False)

    result = ActionExecutor().execute(
        "inspect_process",
        {"pid": os.getpid()},
        mode=ActionMode.REAL,
    )

    assert result.status == ActionStatus.SUCCESS
    assert result.mode == ActionMode.REAL
    assert result.approval_id is None
    assert result.execution["simulated"] is False
    assert result.execution["changed_system_state"] is False
    assert result.execution["inspection"]["available"] is True
    assert result.execution["inspection"]["pid"] == os.getpid()


def test_p13_inspect_process_rejects_invalid_pid() -> None:
    result = ActionExecutor().execute("inspect_process", {"pid": 0})

    assert result.status == ActionStatus.FAILED
    assert result.error == "pid must be a positive integer"

