from __future__ import annotations

import os
from typing import Any

import psutil
from pydantic import Field

from app.schemas import Approval, ApprovalStatus, RiskLevel, StrictBaseModel, utc_now
from tools.process import InspectProcessInput, inspect_process


class ActionMode(str):
    """动作执行模式。"""

    DRY_RUN = "dry_run"
    REAL = "real"


class ActionStatus(str):
    """动作执行结果状态。"""

    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class ActionSpec(StrictBaseModel):
    """可执行动作的说明书。

    ActionExecutor 只会执行这里注册过的动作，避免 LLM 或外部请求随意构造
    未知命令。
    """

    name: str
    description: str
    risk: RiskLevel = RiskLevel.SAFE
    requires_approval: bool = False
    dry_run_supported: bool = True
    real_execution_supported: bool = False
    required_args: list[str] = Field(default_factory=list)


class ActionResult(StrictBaseModel):
    """一次动作执行或 dry-run 的标准化结果。"""

    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    mode: str = ActionMode.DRY_RUN
    status: str
    approval_id: str | None = None
    pre_check: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    post_check: dict[str, Any] = Field(default_factory=dict)
    preview: str | None = None
    error: str | None = None
    created_at: Any = Field(default_factory=utc_now)


class ActionExecutor:
    """动作执行边界。

    P12 默认只 dry-run。P13 只有在显式开关、allowlist、approval、dry-run、
    pre-check 全部通过后，才允许进入 real execution。
    """

    def __init__(
        self,
        specs: dict[str, ActionSpec] | None = None,
        *,
        real_actions_enabled: bool | None = None,
        real_action_allowlist: set[str] | None = None,
    ) -> None:
        self.specs = specs or default_action_specs()
        self.real_actions_enabled = (
            real_actions_enabled
            if real_actions_enabled is not None
            else os.getenv("RESOURCEOPS_ENABLE_REAL_ACTIONS", "").lower() in {"1", "true", "yes", "on"}
        )
        self.real_action_allowlist = (
            real_action_allowlist
            if real_action_allowlist is not None
            else {
                item.strip()
                for item in os.getenv("RESOURCEOPS_REAL_ACTION_ALLOWLIST", "").split(",")
                if item.strip()
            }
        )

    def execute(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        mode: str = ActionMode.DRY_RUN,
        approval: Approval | None = None,
        confirm_real: bool = False,
    ) -> ActionResult:
        args = args or {}
        spec = self.specs.get(action)
        if spec is None:
            return self._blocked(action=action, args=args, mode=mode, approval=approval, error=f"unknown action: {action}")

        approval_error = self._approval_error(spec, approval)
        if approval_error is not None:
            return self._blocked(action=action, args=args, mode=mode, approval=approval, error=approval_error)

        args_error = self._args_error(spec, args)
        if args_error is not None:
            return self._failed(action=action, args=args, mode=mode, approval=approval, error=args_error)

        if mode == ActionMode.DRY_RUN:
            if not spec.dry_run_supported:
                return self._blocked(
                    action=action,
                    args=args,
                    mode=mode,
                    approval=approval,
                    error=f"dry-run is not supported for action: {action}",
                )
            if action == "inspect_process":
                return self._dry_run_inspect_process(args=args)
            if action == "kill_process":
                return self._dry_run_kill_process(args=args, approval=approval)
            if action == "renice_process":
                return self._dry_run_renice_process(args=args, approval=approval)
            return self._blocked(
                action=action,
                args=args,
                mode=mode,
                approval=approval,
                error=f"no dry-run executor implemented for action: {action}",
            )

        if mode == ActionMode.REAL:
            real_error = self._real_execution_error(spec, action, confirm_real)
            if real_error is not None:
                return self._blocked(action=action, args=args, mode=mode, approval=approval, error=real_error)

            dry_run = self.execute(action, args, mode=ActionMode.DRY_RUN, approval=approval)
            if dry_run.status != ActionStatus.SUCCESS:
                return self._blocked(
                    action=action,
                    args=args,
                    mode=mode,
                    approval=approval,
                    error=f"dry-run did not pass: {dry_run.error or dry_run.status}",
                )

            if action == "inspect_process":
                return self._real_inspect_process(args=args, dry_run=dry_run)
            if action == "kill_process":
                return self._real_kill_process(args=args, approval=approval, dry_run=dry_run)
            if action == "renice_process":
                return self._real_renice_process(args=args, approval=approval, dry_run=dry_run)
            return self._blocked(
                action=action,
                args=args,
                mode=mode,
                approval=approval,
                error=f"no real executor implemented for action: {action}",
            )

        return self._blocked(action=action, args=args, mode=mode, approval=approval, error=f"unknown action mode: {mode}")

    def _dry_run_inspect_process(self, *, args: dict[str, Any]) -> ActionResult:
        pid = args["pid"]
        command_preview = args.get("command_preview") or f"inspect_process pid={pid}"
        return ActionResult(
            action="inspect_process",
            args=args,
            mode=ActionMode.DRY_RUN,
            status=ActionStatus.SUCCESS,
            pre_check={
                "passed": True,
                "checks": ["required argument pid is present", "dry-run does not inspect process state"],
            },
            execution={
                "simulated": True,
                "would_execute": command_preview,
                "changed_system_state": False,
            },
            post_check={"passed": True, "checks": ["process state was not read", "dry-run result was produced"]},
            preview=f"dry-run: {command_preview}",
        )

    def _dry_run_kill_process(self, *, args: dict[str, Any], approval: Approval | None) -> ActionResult:
        pid = args["pid"]
        command_preview = args.get("command_preview") or f"kill {pid}"
        return ActionResult(
            action="kill_process",
            args=args,
            mode=ActionMode.DRY_RUN,
            status=ActionStatus.SUCCESS,
            approval_id=approval.approval_id if approval is not None else None,
            pre_check={
                "passed": True,
                "checks": [
                    "approval is present and approved/executed",
                    "required argument pid is present",
                    "dry-run does not send a process signal",
                ],
            },
            execution={
                "simulated": True,
                "would_execute": command_preview,
                "changed_system_state": False,
            },
            post_check={
                "passed": True,
                "checks": ["no process signal was sent", "dry-run result was produced"],
            },
            preview=f"dry-run: {command_preview}",
        )

    def _dry_run_renice_process(self, *, args: dict[str, Any], approval: Approval | None) -> ActionResult:
        pid = args["pid"]
        nice = args["nice"]
        command_preview = args.get("command_preview") or f"renice -n {nice} -p {pid}"
        return ActionResult(
            action="renice_process",
            args=args,
            mode=ActionMode.DRY_RUN,
            status=ActionStatus.SUCCESS,
            approval_id=approval.approval_id if approval is not None else None,
            pre_check={
                "passed": True,
                "checks": [
                    "approval is present and approved/executed",
                    "required arguments pid and nice are present",
                    "dry-run does not change process priority",
                ],
            },
            execution={
                "simulated": True,
                "would_execute": command_preview,
                "changed_system_state": False,
            },
            post_check={
                "passed": True,
                "checks": ["process priority was not changed", "dry-run result was produced"],
            },
            preview=f"dry-run: {command_preview}",
        )

    def _real_execution_error(self, spec: ActionSpec, action: str, confirm_real: bool) -> str | None:
        if not spec.real_execution_supported:
            return f"real execution is not supported for action: {action}"
        if spec.risk == RiskLevel.SAFE:
            return None
        if not self.real_actions_enabled:
            return "real execution is disabled; set RESOURCEOPS_ENABLE_REAL_ACTIONS=true"
        if action not in self.real_action_allowlist:
            return f"real action is not allowlisted: {action}"
        if not confirm_real:
            if spec.risk == RiskLevel.DANGEROUS:
                return "dangerous real action requires confirm_real=True"
            return "real action requires confirm_real=True"
        return None

    def _real_inspect_process(self, *, args: dict[str, Any], dry_run: ActionResult) -> ActionResult:
        pid = args["pid"]
        data = inspect_process(InspectProcessInput(pid=pid))
        success = bool(data.get("available"))
        return ActionResult(
            action="inspect_process",
            args=args,
            mode=ActionMode.REAL,
            status=ActionStatus.SUCCESS if success else ActionStatus.FAILED,
            pre_check={"passed": True, "dry_run_preview": dry_run.preview},
            execution={"simulated": False, "changed_system_state": False, "inspection": data},
            post_check={"passed": True, "available": data.get("available")},
            preview=data.get("preview") or f"inspect_process pid={pid}",
            error=None if success else data.get("error") or "process inspection unavailable",
        )

    def _real_kill_process(self, *, args: dict[str, Any], approval: Approval | None, dry_run: ActionResult) -> ActionResult:
        pid = args["pid"]
        pre_check, proc = self._precheck_process(pid)
        if not pre_check["passed"]:
            return ActionResult(
                action="kill_process",
                args=args,
                mode=ActionMode.REAL,
                status=ActionStatus.BLOCKED,
                approval_id=approval.approval_id if approval else None,
                pre_check=pre_check,
                execution={"simulated": False, "changed_system_state": False},
                post_check={"passed": False},
                preview=f"blocked: kill_process pid={pid}",
                error=pre_check["error"],
            )

        try:
            proc.terminate()
            proc.wait(timeout=5)
            exists_after = psutil.pid_exists(pid)
        except psutil.NoSuchProcess:
            exists_after = False
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            return ActionResult(
                action="kill_process",
                args=args,
                mode=ActionMode.REAL,
                status=ActionStatus.FAILED,
                approval_id=approval.approval_id if approval else None,
                pre_check={**pre_check, "dry_run_preview": dry_run.preview},
                execution={"simulated": False, "changed_system_state": False, "error": str(exc)},
                post_check={"passed": False, "process_exists_after": psutil.pid_exists(pid)},
                preview=f"failed: kill_process pid={pid}",
                error=f"{exc.__class__.__name__}: {exc}",
            )

        success = not exists_after
        return ActionResult(
            action="kill_process",
            args=args,
            mode=ActionMode.REAL,
            status=ActionStatus.SUCCESS if success else ActionStatus.FAILED,
            approval_id=approval.approval_id if approval else None,
            pre_check={**pre_check, "dry_run_preview": dry_run.preview},
            execution={"simulated": False, "signal": "SIGTERM", "changed_system_state": success},
            post_check={"passed": success, "process_exists_after": exists_after},
            preview=f"real: terminated pid={pid}" if success else f"failed: pid={pid} still exists",
            error=None if success else "process still exists after terminate",
        )

    def _real_renice_process(self, *, args: dict[str, Any], approval: Approval | None, dry_run: ActionResult) -> ActionResult:
        pid = args["pid"]
        nice = args["nice"]
        pre_check, proc = self._precheck_process(pid)
        if not pre_check["passed"]:
            return ActionResult(
                action="renice_process",
                args=args,
                mode=ActionMode.REAL,
                status=ActionStatus.BLOCKED,
                approval_id=approval.approval_id if approval else None,
                pre_check=pre_check,
                execution={"simulated": False, "changed_system_state": False},
                post_check={"passed": False},
                preview=f"blocked: renice_process pid={pid}",
                error=pre_check["error"],
            )

        try:
            before_nice = proc.nice()
            proc.nice(nice)
            after_nice = proc.nice()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            return ActionResult(
                action="renice_process",
                args=args,
                mode=ActionMode.REAL,
                status=ActionStatus.FAILED,
                approval_id=approval.approval_id if approval else None,
                pre_check={**pre_check, "dry_run_preview": dry_run.preview},
                execution={"simulated": False, "changed_system_state": False, "error": str(exc)},
                post_check={"passed": False},
                preview=f"failed: renice_process pid={pid}",
                error=f"{exc.__class__.__name__}: {exc}",
            )

        success = after_nice == nice
        return ActionResult(
            action="renice_process",
            args=args,
            mode=ActionMode.REAL,
            status=ActionStatus.SUCCESS if success else ActionStatus.FAILED,
            approval_id=approval.approval_id if approval else None,
            pre_check={**pre_check, "dry_run_preview": dry_run.preview, "before_nice": before_nice},
            execution={
                "simulated": False,
                "changed_system_state": success and before_nice != after_nice,
                "requested_nice": nice,
                "before_nice": before_nice,
                "after_nice": after_nice,
            },
            post_check={"passed": success, "actual_nice": after_nice},
            preview=f"real: reniced pid={pid} nice={after_nice}" if success else f"failed: pid={pid} nice={after_nice}",
            error=None if success else f"process nice value is {after_nice}, expected {nice}",
        )

    def _precheck_process(self, pid: int) -> tuple[dict[str, Any], psutil.Process | None]:
        protected_pids = {0, 1, os.getpid(), os.getppid()}
        if pid in protected_pids:
            return {"passed": False, "error": f"refusing to operate on protected pid: {pid}"}, None

        try:
            proc = psutil.Process(pid)
            username = proc.username()
            name = proc.name()
            cmdline = " ".join(proc.cmdline() or [])
        except psutil.NoSuchProcess:
            return {"passed": False, "error": f"process not found: {pid}"}, None
        except psutil.AccessDenied as exc:
            return {"passed": False, "error": f"process inspection denied: {exc}"}, None

        protected_names = {"systemd", "init", "sshd", "dockerd", "containerd"}
        if username == "root" or name in protected_names:
            return {"passed": False, "error": f"refusing protected process: user={username} name={name}"}, None

        return {"passed": True, "pid": pid, "username": username, "name": name, "cmdline": cmdline}, proc

    def _approval_error(self, spec: ActionSpec, approval: Approval | None) -> str | None:
        if not spec.requires_approval:
            return None
        if approval is None:
            return f"action requires approval: {spec.name}"
        if approval.action != spec.name:
            return f"approval action mismatch: expected {spec.name}, got {approval.action}"
        if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}:
            return f"approval is not approved/executed: {approval.status}"
        return None

    def _args_error(self, spec: ActionSpec, args: dict[str, Any]) -> str | None:
        missing = [name for name in spec.required_args if name not in args]
        if missing:
            return f"missing required args: {missing}"
        if spec.name in {"inspect_process", "kill_process", "renice_process"}:
            pid = args.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                return "pid must be a positive integer"
        if spec.name == "renice_process":
            nice = args.get("nice")
            if not isinstance(nice, int) or nice < -20 or nice > 19:
                return "nice must be an integer between -20 and 19"
        return None

    def _blocked(self, *, action: str, args: dict[str, Any], mode: str, approval: Approval | None, error: str) -> ActionResult:
        return ActionResult(
            action=action,
            args=args,
            mode=mode,
            status=ActionStatus.BLOCKED,
            approval_id=approval.approval_id if approval is not None else None,
            pre_check={"passed": False},
            execution={"simulated": False, "changed_system_state": False},
            post_check={"passed": False},
            preview=f"blocked: {action}",
            error=error,
        )

    def _failed(self, *, action: str, args: dict[str, Any], mode: str, approval: Approval | None, error: str) -> ActionResult:
        return ActionResult(
            action=action,
            args=args,
            mode=mode,
            status=ActionStatus.FAILED,
            approval_id=approval.approval_id if approval is not None else None,
            pre_check={"passed": False},
            execution={"simulated": False, "changed_system_state": False},
            post_check={"passed": False},
            preview=f"failed: {action}",
            error=error,
        )


def default_action_specs() -> dict[str, ActionSpec]:
    inspect = ActionSpec(
        name="inspect_process",
        description="Read process details without changing system state.",
        risk=RiskLevel.SAFE,
        requires_approval=False,
        dry_run_supported=True,
        real_execution_supported=True,
        required_args=["pid"],
    )
    kill_process = ActionSpec(
        name="kill_process",
        description="Terminate a process after approval. Real execution is gated by P13 safety checks.",
        risk=RiskLevel.DANGEROUS,
        requires_approval=True,
        dry_run_supported=True,
        real_execution_supported=True,
        required_args=["pid"],
    )
    renice_process = ActionSpec(
        name="renice_process",
        description="Change a process nice value after approval. Real execution is gated by P13 safety checks.",
        risk=RiskLevel.WRITE,
        requires_approval=True,
        dry_run_supported=True,
        real_execution_supported=True,
        required_args=["pid", "nice"],
    )
    return {
        inspect.name: inspect,
        kill_process.name: kill_process,
        renice_process.name: renice_process,
    }
