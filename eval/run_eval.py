from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult


DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "resource_cases.jsonl"
DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "eval_report.md"


class FixtureRegistry:
    """ToolRegistry-compatible fixture executor for deterministic eval."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture

    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        data = self.fixture.get(name, {})
        preview = data.get("preview", f"{name} fixture") if isinstance(data, dict) else f"{name} fixture"
        summary = data.get("summary", f"{name} fixture") if isinstance(data, dict) else f"{name} fixture"
        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data=data,
            preview=preview,
            summary=summary,
            latency_ms=0,
            validated_args=args or {},
        )


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    expected_findings: list[str]
    actual_findings: list[str]
    missing_findings: list[str]
    approval_required_expected: bool
    approval_required_actual: bool
    status: str
    failed_reasons: list[str]

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
    return cases


def load_fixture(fixtures_dir: Path, fixture_name: str) -> dict[str, Any]:
    path = fixtures_dir / fixture_name
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_case(case: dict[str, Any], fixtures_dir: Path) -> CaseResult:
    fixture = load_fixture(fixtures_dir, case["fixture"])

    with tempfile.TemporaryDirectory() as tmpdir:
        approval_store = ApprovalStore(Path(tmpdir) / "approvals.jsonl")
        approval_service = ApprovalService(store=approval_store)

        result = ResourceAgent(
            registry=FixtureRegistry(fixture),
            approval_service=approval_service,
        ).diagnose(
            ResourceIncident(
                description=case["description"],
                resource_type=case.get("resource_type"),
                severity=case.get("severity", "warning"),
            )
        )

    actual_findings = sorted(finding.finding_type for finding in result.findings)
    expected_findings = sorted(case.get("expected_findings", []))
    missing_findings = sorted(set(expected_findings) - set(actual_findings))

    approval_expected = bool(case.get("approval_required", False))
    approval_actual = bool(result.requires_approval)

    failed_reasons: list[str] = []
    if missing_findings:
        failed_reasons.append(f"missing findings: {', '.join(missing_findings)}")
    if approval_expected != approval_actual:
        failed_reasons.append(
            f"approval_required mismatch: expected={approval_expected}, actual={approval_actual}"
        )

    return CaseResult(
        case_id=case["case_id"],
        passed=not failed_reasons,
        expected_findings=expected_findings,
        actual_findings=actual_findings,
        missing_findings=missing_findings,
        approval_required_expected=approval_expected,
        approval_required_actual=approval_actual,
        status=result.run.status,
        failed_reasons=failed_reasons,
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.passed)

    expected_total = sum(len(item.expected_findings) for item in results)
    missing_total = sum(len(item.missing_findings) for item in results)
    finding_recall = 0.0
    if expected_total:
        finding_recall = round((expected_total - missing_total) / expected_total, 4)

    approval_matches = sum(
        1
        for item in results
        if item.approval_required_expected == item.approval_required_actual
    )

    return {
        "cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "finding_recall": finding_recall,
        "approval_match_rate": round(approval_matches / total, 4) if total else 0.0,
    }


def render_report(overall: dict[str, Any], results: list[CaseResult]) -> str:
    lines = ["# ResourceOps Agent Fixture Eval", "", "## Overall"]
    for key, value in overall.items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Failed Cases"])
    failed = [item for item in results if not item.passed]
    if not failed:
        lines.append("- none")
    else:
        for item in failed:
            lines.append(f"- {item.case_id}: {'; '.join(item.failed_reasons)}")

    lines.extend(["", "## Cases"])
    for item in results:
        lines.append(
            f"- {item.case_id}: passed={item.passed}, "
            f"expected={item.expected_findings}, actual={item.actual_findings}, "
            f"approval={item.approval_required_actual}"
        )

    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ResourceOps fixture eval.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cases = load_cases(args.cases)
    results = [evaluate_case(case, args.fixtures_dir) for case in cases]
    overall = aggregate(results)
    report = render_report(overall, results)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    payload = {
        "overall": overall,
        "cases": [item.model_dump() for item in results],
        "report_path": str(args.report),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report)

    return 0 if overall["passed"] == overall["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
