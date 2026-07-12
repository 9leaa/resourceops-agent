from __future__ import annotations

from pathlib import Path

from actions.executor import ActionResult
from app.schemas import Approval, ApprovalStatus
from trace.store import TraceStore, resolve_trace_db


def resolve_approval_store(path: Path | str | None = None) -> Path:
    """Return the SQLite database used for approvals.

    Approval state now lives in the same SQLite database as trace state. The
    function name is kept for older callers, but RESOURCEOPS_APPROVAL_STORE is
    intentionally ignored.
    """

    return resolve_trace_db(path)


class ApprovalStore:
    """SQLite-backed approval store.

    The public methods intentionally match the old JSONL store so CLI, API and
    tests can move to SQLite without changing every call site at once.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        trace_store: TraceStore | None = None,
    ) -> None:
        self.trace_store = trace_store or TraceStore(resolve_approval_store(path))
        self.path = self.trace_store.path

    def create(self, approval: Approval) -> Approval:
        return self.save(approval)

    def save(self, approval: Approval) -> Approval:
        return self.trace_store.save_approval(approval)

    def get(self, approval_id: str) -> Approval:
        return self.trace_store.get_approval(approval_id)

    def list(
        self,
        status: str | ApprovalStatus | None = "pending",
        *,
        run_id: str | None = None,
    ) -> list[Approval]:
        return self.trace_store.list_approvals(status=status, run_id=run_id)

    def update_status(
        self,
        approval_id: str,
        status: str | ApprovalStatus,
        *,
        decided_at=None,
        executed_at=None,
    ) -> Approval:
        return self.trace_store.update_approval_status(
            approval_id,
            ApprovalStatus(status),
            decided_at=decided_at,
            executed_at=executed_at,
        )

    def claim_for_dry_run(self, approval_id: str) -> Approval:
        return self.trace_store.claim_approval_for_dry_run(approval_id)

    def restore_claim(self, approval_id: str) -> Approval:
        return self.trace_store.restore_approval_claim(approval_id)

    def finalize_action(
        self,
        *,
        approval_id: str,
        action_result: ActionResult,
        expected_statuses: set[ApprovalStatus] | None = None,
    ) -> Approval:
        return self.trace_store.finalize_approval_action(
            approval_id=approval_id,
            action_result=action_result,
            expected_statuses=expected_statuses,
        )

    def reject_pending(self, approval_id: str) -> Approval:
        return self.trace_store.reject_pending_approval(approval_id)
