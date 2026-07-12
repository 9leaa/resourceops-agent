from __future__ import annotations

from typing import Any

from app.schemas import ApprovalStatus
from approval.store import ApprovalStore
from trace.store import TraceStore


def approvals_for_run(run_id: str, store: ApprovalStore) -> list[Any]:
    return [approval for approval in store.list(status=None) if approval.run_id == run_id]


def has_pending_approvals(run_id: str, store: ApprovalStore) -> bool:
    return any(approval.status == ApprovalStatus.PENDING for approval in approvals_for_run(run_id, store))


def sync_approval_trace(
    trace_store: TraceStore,
    approval_store: ApprovalStore,
    approval: Any,
    action_result: Any | None = None,
) -> None:
    """把 approval/action_result 同步到 TraceStore。

    approve/reject 的真实状态源是 ApprovalStore；TraceStore 和 workspace 是
    可查询/可审计视图。P12 起，如果 approve 产生 action_result，也在这里
    一起写入 trace，并更新 Action execution todo。
    """

    try:
        trace_store.apply_approval_transition(
            approval=approval,
            action_result=action_result,
        )
    except KeyError:
        return
