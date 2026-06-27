from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident
from tools.registry import default_registry
from trace.store import TraceStore


def run_live_smoke() -> dict[str, Any]:
    registry = default_registry()
    tool_results: dict[str, dict[str, Any]] = {}

    smoke_tools = [
        ("get_cpu_snapshot", {}),
        ("list_top_cpu_processes", {"limit": 3}),
        ("get_memory_snapshot", {}),
        ("list_top_memory_processes", {"limit": 3}),
        ("check_oom_events", {"limit": 3}),
        ("get_gpu_snapshot", {}),
        ("list_gpu_processes", {"limit": 3}),
        ("inspect_process", {"pid": os.getpid()}),
    ]

    for name, args in smoke_tools:
        result = registry.execute(name, args)
        tool_results[name] = {
            "status": result.status,
            "preview": result.preview,
            "error": result.error,
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        trace_store = TraceStore(Path(tmpdir) / "resourceops.sqlite3")
        approval_store = ApprovalStore(Path(tmpdir) / "approvals.jsonl")
        approval_service = ApprovalService(store=approval_store)

        result = ResourceAgent(
            registry=registry,
            approval_service=approval_service,
        ).diagnose(
            ResourceIncident(description="live smoke resource diagnosis", resource_type="mixed")
        )

        trace_store.save_agent_result(result)
        trace = trace_store.get_trace(result.run.run_id)

    failures = [
        name
        for name, item in tool_results.items()
        if item["status"] not in {"success", "error"}
    ]

    return {
        "passed": not failures and bool(result.final_report) and bool(trace["run"]),
        "tool_results": tool_results,
        "run": result.run.model_dump(mode="json"),
        "findings": [finding.finding_type for finding in result.findings],
        "approvals": len(result.approvals),
        "trace_counts": {
            "steps": len(trace["steps"]),
            "tool_calls": len(trace["tool_calls"]),
            "evidence_items": len(trace["evidence_items"]),
            "findings": len(trace["findings"]),
            "approvals": len(trace["approvals"]),
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ResourceOps live smoke eval.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = run_live_smoke()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("# ResourceOps Live Smoke")
        print(f"passed: {payload['passed']}")
        print(f"run_id: {payload['run']['run_id']}")
        print(f"status: {payload['run']['status']}")
        print(f"findings: {payload['findings']}")
        print(f"approvals: {payload['approvals']}")
        print(f"trace_counts: {payload['trace_counts']}")
        if payload["failures"]:
            print(f"failures: {payload['failures']}")

    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
