from __future__ import annotations

from typing import Any

from app.schemas import ApprovalStatus, RunStatus
from approval.store import ApprovalStore
from trace.store import TraceStore


def approvals_for_run(run_id: str, store: ApprovalStore) -> list[Any]:
    return [approval for approval in store.list(status=None) if approval.run_id == run_id]


def has_pending_approvals(run_id: str, store: ApprovalStore) -> bool:
    return any(approval.status == ApprovalStatus.PENDING for approval in approvals_for_run(run_id, store))


def sync_approval_trace(trace_store: TraceStore, approval_store: ApprovalStore, approval: Any) -> None:
    try:
        trace_store.save_approval(approval)
        approvals = approvals_for_run(approval.run_id, approval_store)
        trace_store.sync_approval_todos(approval.run_id, approvals)

        if not has_pending_approvals(approval.run_id, approval_store):
            trace_store.update_run_status(approval.run_id, RunStatus.COMPLETED)
    except KeyError:
        return