from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from agent.report_reconcile import reconcile_report_snapshot_with_trace
from agent.resource_agent import ResourceAgent
from app.schemas import DiagnosisSnapshot, ReportGenerationStatus, utc_now
from trace.store import TraceStore
from workspace.writer import WorkspaceWriter


def report_worker_count() -> int:
    raw = os.getenv("RESOURCEOPS_REPORT_WORKERS", "2")
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return min(4, max(1, value))


REPORT_EXECUTOR = ThreadPoolExecutor(max_workers=report_worker_count())
REPORT_JOBS: dict[str, Future] = {}
REPORT_JOBS_LOCK = Lock()


def submit_report_job(
    *,
    agent: ResourceAgent,
    snapshot: DiagnosisSnapshot,
    trace_store: TraceStore,
    workspace_writer: WorkspaceWriter,
) -> Future:
    run_id = snapshot.run.run_id
    with REPORT_JOBS_LOCK:
        existing = REPORT_JOBS.get(run_id)
        if existing is not None and not existing.done():
            return existing

        future = REPORT_EXECUTOR.submit(
            run_report_job,
            agent=agent,
            snapshot=snapshot,
            trace_store=trace_store,
            workspace_writer=workspace_writer,
        )
        REPORT_JOBS[run_id] = future
        future.add_done_callback(lambda _future, job_run_id=run_id: remove_report_job(job_run_id))
        return future


def get_report_job(run_id: str) -> Future | None:
    with REPORT_JOBS_LOCK:
        return REPORT_JOBS.get(run_id)


def remove_report_job(run_id: str) -> None:
    with REPORT_JOBS_LOCK:
        REPORT_JOBS.pop(run_id, None)


def cleanup_finished_jobs() -> None:
    with REPORT_JOBS_LOCK:
        finished = [run_id for run_id, future in REPORT_JOBS.items() if future.done()]
        for run_id in finished:
            REPORT_JOBS.pop(run_id, None)


def run_report_job(
    *,
    agent: ResourceAgent,
    snapshot: DiagnosisSnapshot,
    trace_store: TraceStore,
    workspace_writer: WorkspaceWriter,
) -> None:
    run_id = snapshot.run.run_id
    trace_store.update_report_status(
        run_id,
        ReportGenerationStatus.GENERATING,
        started_at=utc_now(),
    )

    try:
        report = agent.generate_report(snapshot, emit_events=False)
        latest_trace = trace_store.get_trace(run_id)
        report = reconcile_report_snapshot_with_trace(report, latest_trace)

        trace_store.finalize_report_snapshot(
            report,
            report_status=final_report_status(report),
            finished_at=utc_now(),
        )
        workspace_writer.apply_report_snapshot(report, trace_store=trace_store)
    except Exception as exc:
        trace_store.mark_report_failed(run_id, str(exc))
        raise


def final_report_status(report) -> ReportGenerationStatus:
    if report.status == "fallback":
        return ReportGenerationStatus.FALLBACK
    if report.status == "success":
        return ReportGenerationStatus.READY
    return ReportGenerationStatus.FAILED


def recover_interrupted_report_jobs(trace_store: TraceStore | None = None) -> None:
    trace_store = trace_store or TraceStore()
    trace_store.fail_generating_reports("service restarted during report generation")
