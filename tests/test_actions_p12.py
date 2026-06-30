from actions.executor import ActionExecutor, ActionMode, ActionStatus
from app.schemas import Approval, ApprovalStatus, RiskLevel


def make_executed_approval() -> Approval:
    return Approval(
        run_id="run_test",
        action="kill_process",
        args={"pid": 12345, "command_preview": "kill 12345"},
        reason="test approval",
        risk=RiskLevel.DANGEROUS,
        status=ApprovalStatus.EXECUTED,
    )


def test_p12_action_executor_dry_runs_kill_process() -> None:
    approval = make_executed_approval()

    result = ActionExecutor().execute(
        "kill_process",
        {"pid": 12345, "command_preview": "kill 12345"},
        approval=approval,
    )

    assert result.action == "kill_process"
    assert result.mode == ActionMode.DRY_RUN
    assert result.status == ActionStatus.SUCCESS
    assert result.approval_id == approval.approval_id
    assert result.execution["simulated"] is True
    assert result.execution["changed_system_state"] is False
    assert result.execution["would_execute"] == "kill 12345"
    assert "dry-run" in result.preview


def test_p12_action_executor_blocks_dangerous_action_without_approval() -> None:
    result = ActionExecutor().execute(
        "kill_process",
        {"pid": 12345},
    )

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "action requires approval: kill_process"
    assert result.execution["changed_system_state"] is False


def test_p12_action_executor_blocks_pending_approval() -> None:
    approval = Approval(
        run_id="run_test",
        action="kill_process",
        args={"pid": 12345},
        reason="test approval",
        risk=RiskLevel.DANGEROUS,
        status=ApprovalStatus.PENDING,
    )

    result = ActionExecutor().execute(
        "kill_process",
        {"pid": 12345},
        approval=approval,
    )

    assert result.status == ActionStatus.BLOCKED
    assert "approval is not approved/executed" in result.error


def test_p12_action_executor_rejects_invalid_args() -> None:
    approval = make_executed_approval()

    result = ActionExecutor().execute(
        "kill_process",
        {"pid": -1},
        approval=approval,
    )

    assert result.status == ActionStatus.FAILED
    assert result.error == "pid must be a positive integer"


def test_p12_action_executor_blocks_unknown_action() -> None:
    result = ActionExecutor().execute(
        "restart_database",
        {"service": "postgres"},
    )

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "unknown action: restart_database"


def test_p12_action_executor_blocks_real_execution_by_default() -> None:
    approval = make_executed_approval()

    result = ActionExecutor().execute(
        "kill_process",
        {"pid": 12345},
        mode=ActionMode.REAL,
        approval=approval,
        confirm_real=True,
    )

    assert result.status == ActionStatus.BLOCKED
    assert result.error == "real execution is disabled; set RESOURCEOPS_ENABLE_REAL_ACTIONS=true"
    assert result.execution["changed_system_state"] is False
