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
    """Compatibility sync for older callers that still invoke trace sync.

    Phase 2 moves approval transitions into TraceStore transactions. This
    helper remains idempotent so CLI/API call sites from earlier phases can
    still refresh derived todos and reports without duplicating action rows.
    """

    try:
        trace_store.apply_approval_transition(
            approval=approval,
            action_result=action_result,
        )
    except KeyError:
        return
