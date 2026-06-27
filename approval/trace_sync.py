from __future__ import annotations

from typing import Any

from app.schemas import RunStatus
from approval.store import ApprovalStore
from trace.store import TraceStore


def has_pending_approvals(run_id: str, store: ApprovalStore) -> bool:
    """判断某个 run 是否还有待处理审批。"""

    return any(approval.run_id == run_id for approval in store.list(status="pending"))


def sync_approval_trace(trace_store: TraceStore, approval_store: ApprovalStore, approval: Any) -> None:
    """把 approval 的最新状态同步回 trace。

    CLI 和 HTTP 都会调用这个函数。若某次本地 demo 删除了 trace DB，但
    approvals.jsonl 还保留旧审批，这里不让同步失败影响审批结果。
    """

    try:
        trace_store.save_approval(approval)
        if not has_pending_approvals(approval.run_id, approval_store):
            trace_store.update_run_status(approval.run_id, RunStatus.COMPLETED)
    except KeyError:
        return
