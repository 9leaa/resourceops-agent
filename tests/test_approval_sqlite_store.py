from __future__ import annotations

from pathlib import Path

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import Approval, ApprovalStatus, ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


def make_approval(status: ApprovalStatus = ApprovalStatus.PENDING) -> Approval:
    return Approval(
        approval_id="appr_sqlite_test",
        run_id="run_sqlite_test",
        action="kill_process",
        args={"pid": 123, "command_preview": "kill 123"},
        reason="test approval",
        status=status,
    )


def test_sqlite_approval_store_create_get_and_list(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "resourceops.sqlite3")
    approval = store.save(make_approval())

    assert store.get(approval.approval_id).approval_id == approval.approval_id
    assert [item.approval_id for item in store.list(status="pending")] == [approval.approval_id]
    assert store.list(status="executed") == []


def test_sqlite_approval_store_updates_status(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "resourceops.sqlite3")
    approval = store.save(make_approval())

    executed = store.update_status(approval.approval_id, ApprovalStatus.EXECUTED)

    assert executed.status == ApprovalStatus.EXECUTED
    assert store.get(approval.approval_id).status == ApprovalStatus.EXECUTED
    assert store.list(status="pending") == []
    assert [item.approval_id for item in store.list(status="executed")] == [approval.approval_id]


def test_pending_snapshot_does_not_overwrite_terminal_approval(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "resourceops.sqlite3")
    approval = store.save(make_approval())
    store.update_status(approval.approval_id, ApprovalStatus.EXECUTED)

    stale_pending = make_approval(status=ApprovalStatus.PENDING)
    stale_pending = stale_pending.model_copy(update={"reason": "stale snapshot"})
    saved = store.save(stale_pending)

    assert saved.status == ApprovalStatus.EXECUTED
    assert saved.reason == "test approval"


def test_resource_agent_runs_without_approvals_jsonl(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "resourceops.sqlite3"
    old_jsonl = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("RESOURCEOPS_TRACE_DB", str(db_path))
    monkeypatch.delenv("RESOURCEOPS_APPROVAL_STORE", raising=False)

    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval_service = ApprovalService(store=approval_store)
    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))

    trace_store.save_agent_result(result)
    approval_id = result.approvals[0]["approval_id"]

    assert not old_jsonl.exists()
    assert approval_store.get(approval_id).status == ApprovalStatus.PENDING
    assert trace_store.get_approval(approval_id).status == ApprovalStatus.PENDING
