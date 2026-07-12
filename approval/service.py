from __future__ import annotations

from typing import Any

from actions.executor import ActionExecutor, ActionMode, ActionResult, ActionStatus
from app.schemas import Approval, ApprovalStatus, RiskLevel, ToolCallStatus, ToolPermissionLevel, utc_now
from approval.store import ApprovalStore
from tools.registry import ToolExecutionResult


class ApprovalService:
    """危险操作审批服务。

    V1-P4 中，ResourceAgent 会为 dangerous recommendation 创建审批。
    P12 后，approve 会先进入 ActionExecutor dry-run，再把 ActionResult
    返回给 trace/workspace/CLI/API。
    """
    def __init__(
        self,
        store: ApprovalStore | None = None,
        action_executor: ActionExecutor | None = None,
    ) -> None:
        self.store = store or ApprovalStore()
        self.action_executor = action_executor or ActionExecutor()

    def request_approval(
        self,
        run_id: str,
        action: str,
        args: dict[str, Any],
        reason: str,
        risk: RiskLevel = RiskLevel.DANGEROUS,
        *,
        persist: bool = True,
    ) -> Approval:
        """Create an approval, preserving the old persisted-by-default API."""

        approval = self.build_approval(
            run_id=run_id,
            action=action,
            args=args,
            reason=reason,
            risk=risk,
        )
        if persist:
            return self.store.save(approval)
        return approval

    def build_approval(
        self,
        *,
        run_id: str,
        action: str,
        args: dict[str, Any],
        reason: str,
        risk: RiskLevel = RiskLevel.DANGEROUS,
    ) -> Approval:
        """Build an approval object without persisting it."""

        return Approval(
            run_id=run_id,
            action=action,
            args=args,
            reason=reason,
            risk=risk,
        )

    def list_pending(self) -> list[Approval]:
        """列出带审批的记录"""
        return self.store.list(status=ApprovalStatus.PENDING.value)

    def approve(self, approval_id: str) -> tuple[Approval, ToolExecutionResult]:
        """兼容旧调用方，只返回 approval 和旧 tool_result 形状。"""

        approval, tool_result, _action_result = self.approve_with_action_result(approval_id)
        return approval, tool_result

    def approve_with_action_result(self, approval_id: str) -> tuple[Approval, ToolExecutionResult, ActionResult]:
        """批准审批并执行 dry-run action。

        状态流是 pending -> approved -> executed。这里的 executed 表示
        dry-run action 已成功完成，不表示真实危险操作已经执行。
        """

        approval = self.store.get(approval_id)
        self._require_pending(approval)

        # 先进入 approved，让 ActionExecutor 能明确拿到“已批准”的审批对象。
        approval = self.store.update_status(
            approval_id,
            ApprovalStatus.APPROVED,
            decided_at=utc_now(),
        )

        action_result = self.action_executor.execute(
            approval.action,
            approval.args,
            mode=ActionMode.DRY_RUN,
            approval=approval,
        )

        if action_result.status == ActionStatus.SUCCESS:
            # P12 的 executed 代表 dry-run 执行完成；真实执行仍留给 P13。
            approval = self.store.update_status(
                approval_id,
                ApprovalStatus.EXECUTED,
                executed_at=utc_now(),
            )

        tool_result = self._tool_result_from_action_result(approval, action_result)
        return approval, tool_result, action_result

    def execute_real_approved_action(
        self,
        approval_id: str,
        *,
        confirm_real: bool = False,
    ) -> tuple[Approval, ToolExecutionResult, ActionResult]:
        approval = self.store.get(approval_id)
        if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}:
            raise ValueError(f"approval {approval.approval_id} is not approved/executed: {approval.status}")

        action_result = self.action_executor.execute(
            approval.action,
            approval.args,
            mode=ActionMode.REAL,
            approval=approval,
            confirm_real=confirm_real,
        )

        if action_result.status == ActionStatus.SUCCESS:
            approval = self.store.update_status(
                approval_id,
                ApprovalStatus.EXECUTED,
                executed_at=utc_now(),
            )

        tool_result = self._tool_result_from_action_result(approval, action_result)
        return approval, tool_result, action_result

    def _tool_result_from_action_result(
        self,
        approval: Approval,
        action_result: ActionResult,
    ) -> ToolExecutionResult:
        """把 ActionResult 包装成旧 ToolExecutionResult，保持 API/测试兼容。"""

        success = action_result.status == ActionStatus.SUCCESS
        simulated = action_result.mode == ActionMode.DRY_RUN
        summary = (
            "dangerous action was approved and dry-run executed"
            if simulated and success
            else "dangerous action was approved and real execution completed"
            if success
            else action_result.error
        )
        return ToolExecutionResult(
            tool_name=approval.action,
            permission_level=self._tool_permission_level(approval),
            status=ToolCallStatus.SUCCESS if success else ToolCallStatus.ERROR,
            data={
                "simulated": simulated,
                "action": approval.action,
                "args": approval.args,
                "action_result": action_result.model_dump(mode="json"),
            },
            preview=action_result.preview,
            summary=summary,
            error=None if success else action_result.error,
            latency_ms=0,
            validated_args=approval.args,
        )


    @staticmethod
    def _tool_permission_level(approval: Approval) -> ToolPermissionLevel:
        try:
            return ToolPermissionLevel(str(approval.risk))
        except ValueError:
            return ToolPermissionLevel.DANGEROUS


    def reject(self, approval_id: str) -> Approval:
        approval = self.store.get(approval_id)
        self._require_pending(approval)
        return self.store.update_status(
            approval_id,
            ApprovalStatus.REJECTED,
            decided_at=utc_now(),
        )

    @staticmethod
    def _require_pending(approval: Approval) -> None:
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval.approval_id} is not pending: {approval.status}")
