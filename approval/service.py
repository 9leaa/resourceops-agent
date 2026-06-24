from __future__ import annotations

from typing import Any

from app.schemas import Approval, ApprovalStatus, RiskLevel, ToolCallStatus, ToolPermissionLevel, utc_now
from approval.store import ApprovalStore
from tools.registry import ToolExecutionResult


class ApprovalService:
    """危险操作审批服务。

    V1-P4 中，ResourceAgent 会为 dangerous recommendation 创建审批。
    approve 后仍然只模拟执行，不会真实 kill 进程。
    """

    def __init__(self, store: ApprovalStore | None = None) -> None:
        self.store = store or ApprovalStore()

    def request_approval(
        self,
        run_id: str,
        action: str,
        args: dict[str, Any],
        reason: str,
        risk: RiskLevel = RiskLevel.DANGEROUS,
    ) -> Approval:
        """
        创建审批并保存
        args：
            哪一次诊断运行
            待审批动作名称
            动作参数
            为什么需要这个动作
            风险等级：默认dangerous
        """
        return self.store.save(
            Approval(
                run_id=run_id,
                action=action,
                args=args,
                reason=reason,
                risk=risk,
            )
        )

    def list_pending(self) -> list[Approval]:
        """列出带审批的记录"""
        return self.store.list(status=ApprovalStatus.PENDING.value)

    def approve(self, approval_id: str) -> tuple[Approval, ToolExecutionResult]:
        approval = self.store.get(approval_id)
        self._require_pending(approval)
        approval.status = ApprovalStatus.EXECUTED
        approval.decided_at = utc_now()
        approval.executed_at = approval.decided_at
        approval = self.store.save(approval)

        #TODO这里是pending-executed  还可以改成 pending - approved - executed
        result = ToolExecutionResult(
            tool_name=approval.action,
            permission_level=ToolPermissionLevel.DANGEROUS,
            status=ToolCallStatus.SUCCESS,
            data={"simulated": True, "action": approval.action, "args": approval.args},
            preview=f"simulated execution: {approval.action}",
            summary="dangerous action was approved and simulated",
            latency_ms=0,
            validated_args=approval.args,
        )
        return approval, result

    def reject(self, approval_id: str) -> Approval:
        approval = self.store.get(approval_id)
        self._require_pending(approval)
        approval.status = ApprovalStatus.REJECTED
        approval.decided_at = utc_now()
        return self.store.save(approval)

    @staticmethod
    def _require_pending(approval: Approval) -> None:
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval.approval_id} is not pending: {approval.status}")
