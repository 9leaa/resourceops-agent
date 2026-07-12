from __future__ import annotations

from pathlib import Path

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
