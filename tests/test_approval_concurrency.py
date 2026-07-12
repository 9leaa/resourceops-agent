from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import time

import pytest

from actions.executor import ActionExecutor
from agent.resource_agent import ResourceAgent
from approval.errors import ApprovalTransitionConflict
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from tests.fixtures import MemoryPressureRegistry
from trace.store import TraceStore


class SlowCountingExecutor(ActionExecutor):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.calls = 0
        self._lock = Lock()

    def execute(self, *args, **kwargs):
        with self._lock:
            self.calls += 1
        time.sleep(0.05)
        if self.fail:
            raise RuntimeError("forced action executor failure")
        return super().execute(*args, **kwargs)


def build_saved_memory_run(tmp_path, action_executor=None):
    trace_store = TraceStore(tmp_path / "trace.sqlite3")
    approval_store = ApprovalStore(trace_store=trace_store)
    approval_service = ApprovalService(
        store=approval_store,
        action_executor=action_executor,
    )
    result = ResourceAgent(
        registry=MemoryPressureRegistry(),
        approval_service=approval_service,
    ).diagnose(ResourceIncident(description="为什么内存快满了？", resource_type="memory"))
    trace_store.save_agent_result(result)
    return trace_store, approval_service, result.run.run_id, result.approvals[0]["approval_id"]


def collect_thread_results(futures):
    successes = []
    conflicts = []
    errors = []
    for future in futures:
        try:
            successes.append(future.result(timeout=5))
        except ApprovalTransitionConflict as exc:
            conflicts.append(exc)
        except Exception as exc:  # pragma: no cover - failure diagnostics.
            errors.append(exc)
    return successes, conflicts, errors


def test_concurrent_approve_executes_action_once(tmp_path) -> None:
    executor = SlowCountingExecutor()
    trace_store, approval_service, run_id, approval_id = build_saved_memory_run(tmp_path, executor)
    barrier = Barrier(2)

    def approve_once():
        barrier.wait(timeout=5)
        return approval_service.approve_with_action_result(approval_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve_once) for _ in range(2)]
        successes, conflicts, errors = collect_thread_results(futures)

    assert errors == []
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert executor.calls == 1

    trace = trace_store.get_trace(run_id)
    assert trace["approvals"][0]["status"] == "executed"
    assert len(trace["action_results"]) == 1
    assert trace["action_results"][0]["status"] == "success"


def test_concurrent_approve_and_reject_only_one_wins(tmp_path) -> None:
    executor = SlowCountingExecutor()
    trace_store, approval_service, run_id, approval_id = build_saved_memory_run(tmp_path, executor)
    barrier = Barrier(2)

    def approve_once():
        barrier.wait(timeout=5)
        return approval_service.approve_with_action_result(approval_id)

    def reject_once():
        barrier.wait(timeout=5)
        return approval_service.reject(approval_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve_once), pool.submit(reject_once)]
        successes, conflicts, errors = collect_thread_results(futures)

    assert errors == []
    assert len(successes) == 1
    assert len(conflicts) == 1

    trace = trace_store.get_trace(run_id)
    final_status = trace["approvals"][0]["status"]
    assert final_status in {"executed", "rejected"}
    if final_status == "executed":
        assert executor.calls == 1
        assert len(trace["action_results"]) == 1
    else:
        assert executor.calls == 0
        assert trace["action_results"] == []


def test_action_executor_exception_restores_pending_without_fake_completion(tmp_path) -> None:
    executor = SlowCountingExecutor(fail=True)
    trace_store, approval_service, run_id, approval_id = build_saved_memory_run(tmp_path, executor)

    with pytest.raises(RuntimeError, match="forced action executor failure"):
        approval_service.approve_with_action_result(approval_id)

    trace = trace_store.get_trace(run_id)
    assert trace["approvals"][0]["status"] == "pending"
    assert trace["action_results"] == []
    assert trace["run"]["status"] == "waiting_approval"
    assert [
        todo for todo in trace["todos"]
        if todo.get("source") == "action_executor"
    ] == []


def test_finalize_save_failure_does_not_mark_executed_or_complete(tmp_path, monkeypatch) -> None:
    executor = SlowCountingExecutor()
    trace_store, approval_service, run_id, approval_id = build_saved_memory_run(tmp_path, executor)

    def fail_save_action_result(*_args, **_kwargs):
        raise RuntimeError("forced action result failure")

    monkeypatch.setattr(trace_store, "save_action_result", fail_save_action_result)

    with pytest.raises(RuntimeError, match="forced action result failure"):
        approval_service.approve_with_action_result(approval_id)

    trace = trace_store.get_trace(run_id)
    assert trace["approvals"][0]["status"] == "approved"
    assert trace["action_results"] == []
    assert trace["run"]["status"] == "waiting_approval"
    assert [
        todo for todo in trace["todos"]
        if todo.get("source") == "action_executor"
    ] == []
