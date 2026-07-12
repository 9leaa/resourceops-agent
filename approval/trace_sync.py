from __future__ import annotations

from typing import Any

from app.schemas import ApprovalStatus, RunStatus
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
        trace_store.save_approval(approval)

        if action_result is not None:
            # action_result 是 P12 的动作审计记录，和 approval 状态分开保存。
            trace_store.save_action_result(approval.run_id, action_result)
            trace_store.sync_action_todos(approval.run_id, action_result)

        approvals = approvals_for_run(approval.run_id, approval_store)
        trace_store.sync_approval_todos(approval.run_id, approvals)

        if not has_pending_approvals(approval.run_id, approval_store):
            if action_result is not None and str(action_result.status) != "success":
                # dry-run 失败时不能把 run 标成 completed。
                trace_store.update_run_status(approval.run_id, RunStatus.FAILED)
            else:
                trace_store.update_run_status(approval.run_id, RunStatus.COMPLETED)

        trace_store.reconcile_run_report(approval.run_id)
    except KeyError:
        return
