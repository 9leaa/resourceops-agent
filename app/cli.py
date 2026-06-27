from __future__ import annotations

import argparse
import json
from typing import Sequence

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import IncidentSource, ResourceIncident, ResourceType, Severity
from trace.store import TraceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resourceops", description="ResourceOps Agent local CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose_parser = subparsers.add_parser("diagnose", help="Create a local resource diagnosis run.")
    diagnose_parser.add_argument("description", help="Resource problem description")
    diagnose_parser.add_argument(
        "--resource-type",
        choices=[item.value for item in ResourceType],
        default=None,
        help="Optional target resource scope.",
    )
    diagnose_parser.add_argument(
        "--severity",
        choices=[item.value for item in Severity],
        default=Severity.WARNING.value,
        help="Diagnosis severity.",
    )
    diagnose_parser.add_argument("--host", default=None, help="Optional host name.")
    diagnose_parser.add_argument(
        "--agent-mode",
        default="deterministic",
        choices=["deterministic", "llm_report"],
        help="Agent mode. V1-P3 supports deterministic planning, tool execution, and detectors.",
    )
    diagnose_parser.add_argument("--json", action="store_true", help="Print structured JSON output.")
    diagnose_parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only print the normalized ResourceIncident and skip diagnosis.",
    )

    runs_parser = subparsers.add_parser("runs", help="List recent diagnosis runs.")
    runs_parser.add_argument("--limit", type=int, default=20)
    runs_parser.add_argument("--json", action="store_true")

    trace_parser = subparsers.add_parser("trace", help="Show a traced diagnosis run.")
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--json", action="store_true")

    approvals_parser = subparsers.add_parser("approvals", help="List pending approvals.")
    approvals_parser.add_argument("--json", action="store_true")

    approve_parser = subparsers.add_parser("approve", help="Approve and simulate a dangerous action.")
    approve_parser.add_argument("approval_id")
    approve_parser.add_argument("--json", action="store_true")

    reject_parser = subparsers.add_parser("reject", help="Reject a pending dangerous action.")
    reject_parser.add_argument("approval_id")
    reject_parser.add_argument("--json", action="store_true")

    return parser


def handle_diagnose(args: argparse.Namespace) -> int:
    incident = ResourceIncident(
        description=args.description,
        resource_type=args.resource_type,
        severity=args.severity,
        source=IncidentSource.CLI,
        host=args.host,
    )
    if args.json_only:
        print(json.dumps(incident.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    trace_store = TraceStore()
    agent = ResourceAgent(approval_service=ApprovalService(), agent_mode=args.agent_mode)
    result = agent.diagnose(incident)
    trace_store.save_agent_result(result)

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(result.final_report)
        print(f"\nrun_id={result.run.run_id}")
    return 0


def handle_runs(args: argparse.Namespace) -> int:
    runs = TraceStore().list_runs(limit=args.limit)
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2))
    elif runs:
        for run in runs:
            print(f"{run['run_id']} {run['status']} {run['resource_type']} {run['user_input']}")
    else:
        print("当前没有 diagnosis runs。")
    return 0


def handle_trace(args: argparse.Namespace) -> int:
    trace = TraceStore().get_trace(args.run_id)
    if args.json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        run = trace["run"]
        print(f"run_id={run['run_id']} status={run['status']} resource_type={run['resource_type']}")
        print(f"user_input={run['user_input']}")
        print(f"summary={run['summary']}")
        print("\nsteps:")
        for step in trace["steps"]:
            print(f"- #{step['step_index']} {step['action']} preview={step['observation_preview']}")
        print("\nfindings:")
        if trace["findings"]:
            for finding in trace["findings"]:
                print(f"- {finding['finding_type']} confidence={finding['confidence']}")
        else:
            print("- none")
        print("\napprovals:")
        if trace["approvals"]:
            for approval in trace["approvals"]:
                print(
                    f"- {approval['approval_id']} {approval['action']} "
                    f"status={approval['status']} risk={approval['risk']}"
                )
        else:
            print("- none")
    return 0


def handle_approvals(args: argparse.Namespace) -> int:
    approvals = [approval.model_dump(mode="json") for approval in ApprovalStore().list(status="pending")]
    if args.json:
        print(json.dumps(approvals, ensure_ascii=False, indent=2))
    elif approvals:
        for approval in approvals:
            print(f"{approval['approval_id']} {approval['action']} {approval['args']} reason={approval['reason']}")
    else:
        print("当前没有 pending approval。")
    return 0


def handle_approve(args: argparse.Namespace) -> int:
    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval, tool_result = ApprovalService(store=approval_store).approve(args.approval_id)
    sync_approval_trace(trace_store, approval_store, approval)
    payload = {"approval": approval.model_dump(mode="json"), "tool_result": tool_result.model_dump(mode="json")}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"已批准并模拟执行：{approval.action} {approval.args}")
    return 0


def handle_reject(args: argparse.Namespace) -> int:
    trace_store = TraceStore()
    approval_store = ApprovalStore()
    approval = ApprovalService(store=approval_store).reject(args.approval_id)
    sync_approval_trace(trace_store, approval_store, approval)
    if args.json:
        print(json.dumps(approval.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"已拒绝审批：{approval.approval_id} status={approval.status}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "diagnose":
        return handle_diagnose(args)
    if args.command == "runs":
        return handle_runs(args)
    if args.command == "trace":
        return handle_trace(args)
    if args.command == "approvals":
        return handle_approvals(args)
    if args.command == "approve":
        return handle_approve(args)
    if args.command == "reject":
        return handle_reject(args)
    parser.error(f"unknown command: {args.command}")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
