from __future__ import annotations

import json
import hashlib
import os
import tarfile
from pathlib import Path
from typing import Any

from agent.report_reconcile import reconcile_report_text_with_trace
from app.schemas import DiagnosisSnapshot, ReportSnapshot, ResourceAgentResult
from trace.llm_calls import extract_llm_calls, public_llm_call
from trace.summary import build_run_summary, render_run_summary_markdown


DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[1] / "var" / "runs"
DEFAULT_BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "var" / "bundles"
WORKSPACE_VERSION = "p14"


def resolve_workspace_root(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("RESOURCEOPS_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT))


def resolve_bundle_root(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("RESOURCEOPS_BUNDLE_ROOT", DEFAULT_BUNDLE_ROOT))


class WorkspaceWriter:
    """把一次 ResourceAgentResult 写成独立 run workspace。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = resolve_workspace_root(root)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def write_agent_result(self, result: ResourceAgentResult) -> Path:
        run_dir = self.run_dir(result.run.run_id)

        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        (run_dir / "compact").mkdir(parents=True, exist_ok=True)
        (run_dir / "summary").mkdir(parents=True, exist_ok=True)
        (run_dir / "trace").mkdir(parents=True, exist_ok=True)

        self._write_json(run_dir / "metadata.json", self._metadata(result))
        self._write_json(run_dir / "plan.json", self._tool_plan(result))
        self._write_json(run_dir / "todos.json", [self._jsonable(todo) for todo in result.todos])
        self._write_text(run_dir / "report.md", result.final_report)

        self._write_jsonl(
            run_dir / "raw" / "tool_outputs.jsonl",
            [self._jsonable(tool_result) for tool_result in result.tool_results],
        )

        self._write_json(run_dir / "compact" / "report_context.json", self._report_context(result))

        compact_steps = self._compact_steps([self._jsonable(step) for step in result.steps])
        self._write_json(run_dir / "trace" / "steps.json", compact_steps)
        self._write_json(run_dir / "trace" / "evidence.json", [self._jsonable(item) for item in result.evidence_items])
        self._write_json(run_dir / "trace" / "findings.json", [self._jsonable(item) for item in result.findings])
        self._write_json(run_dir / "trace" / "approvals.json", [self._jsonable(item) for item in result.approvals])
        self._write_json(run_dir / "trace" / "action_results.json", [])
        self._write_llm_call_files(run_dir, [self._jsonable(step) for step in result.steps])
        self._write_summary_files(run_dir, build_run_summary(self._trace_from_result(result)))

        return run_dir

    def write_diagnosis_snapshot(self, snapshot: DiagnosisSnapshot) -> Path:
        run_dir = self.run_dir(snapshot.run.run_id)

        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        (run_dir / "compact").mkdir(parents=True, exist_ok=True)
        (run_dir / "summary").mkdir(parents=True, exist_ok=True)
        (run_dir / "trace").mkdir(parents=True, exist_ok=True)

        self._write_json(run_dir / "metadata.json", self._metadata_from_snapshot(snapshot))
        self._write_json(run_dir / "plan.json", self._tool_plan_from_snapshot(snapshot))
        self._write_json(run_dir / "todos.json", [self._jsonable(todo) for todo in snapshot.todos])
        self._write_text(run_dir / "report.md", "Report is still generating.")
        self._write_jsonl(
            run_dir / "raw" / "tool_outputs.jsonl",
            [self._jsonable(tool_result) for tool_result in snapshot.tool_results],
        )
        self._write_json(
            run_dir / "compact" / "report_context.json",
            {"available": False, "reason": "report has not been generated yet", "report_mode": snapshot.run.report_mode},
        )
        compact_steps = self._compact_steps([self._jsonable(step) for step in snapshot.steps])
        self._write_json(run_dir / "trace" / "steps.json", compact_steps)
        self._write_json(run_dir / "trace" / "evidence.json", [self._jsonable(item) for item in snapshot.evidence_items])
        self._write_json(run_dir / "trace" / "findings.json", [self._jsonable(item) for item in snapshot.findings])
        self._write_json(run_dir / "trace" / "approvals.json", [self._jsonable(item) for item in snapshot.approvals])
        self._write_json(run_dir / "trace" / "action_results.json", [])
        self._write_llm_call_files(run_dir, [self._jsonable(step) for step in snapshot.steps])
        self._write_summary_files(run_dir, build_run_summary(self._trace_from_snapshot(snapshot)))
        return run_dir

    def apply_report_snapshot(self, report: ReportSnapshot, trace_store: Any | None = None) -> Path:
        run_dir = self.run_dir(report.run_id)
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {run_dir}")

        self._write_text(run_dir / "report.md", report.final_report)

        if trace_store is not None:
            trace = trace_store.get_trace(report.run_id)
            self._write_json(run_dir / "metadata.json", self._metadata_from_trace(trace, run_dir))
            self._write_json(run_dir / "todos.json", trace.get("todos") or [])
            self._write_json(run_dir / "trace" / "steps.json", self._compact_steps(trace.get("steps") or []))
            self._write_json(run_dir / "trace" / "approvals.json", trace.get("approvals") or [])
            self._write_json(run_dir / "trace" / "action_results.json", trace.get("action_results") or [])
            self._write_llm_call_files(run_dir, trace.get("steps") or [])
            self._write_summary_files(run_dir, build_run_summary(trace))
            self._write_remediation_summary(run_dir, trace)
        else:
            existing_steps = read_json_file(run_dir / "trace" / "steps.json") if (run_dir / "trace" / "steps.json").exists() else []
            self._write_json(run_dir / "trace" / "steps.json", existing_steps + self._compact_steps([self._jsonable(step) for step in report.steps]))
            self._write_llm_call_files(run_dir, [self._jsonable(step) for step in report.steps])
        return run_dir

    def update_from_trace(self, run_id: str, trace_store: Any) -> Path:
        run_dir = self.run_dir(run_id)
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {run_dir}")

        trace = trace_store.get_trace(run_id)
        self._write_reconciled_report_from_trace(run_dir, trace)
        self._write_json(run_dir / "metadata.json", self._metadata_from_trace(trace, run_dir))
        self._write_json(run_dir / "todos.json", trace.get("todos") or [])
        self._write_json(run_dir / "trace" / "approvals.json", trace.get("approvals") or [])
        # P12 approve 后会新增 action_result 和 action todo，这里跟随 trace 刷新。
        self._write_json(run_dir / "trace" / "action_results.json", trace.get("action_results") or [])
        self._write_llm_call_files(run_dir, trace.get("steps") or [])
        self._write_summary_files(run_dir, build_run_summary(trace))
        self._write_remediation_summary(run_dir, trace)
        return run_dir

    def _write_reconciled_report_from_trace(self, run_dir: Path, trace: dict[str, Any]) -> None:
        final_report = (trace.get("run") or {}).get("final_report")
        if not final_report:
            return

        self._write_text(
            run_dir / "report.md",
            reconcile_report_text_with_trace(str(final_report), trace),
        )

    def create_bundle(
        self,
        run_id: str,
        bundle_root: Path | str | None = None,
        *,
        include_llm_payloads: bool = False,
    ) -> Path:
        run_dir = self.run_dir(run_id)
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {run_dir}")

        output_root = resolve_bundle_root(bundle_root)
        output_root.mkdir(parents=True, exist_ok=True)
        bundle_path = output_root / f"{run_id}.tar.gz"
        with tarfile.open(bundle_path, "w:gz") as archive:
            archive.add(
                run_dir,
                arcname=f"runs/{run_id}",
                filter=lambda info: self._tar_filter(info, include_llm_payloads=include_llm_payloads),
            )
        return bundle_path

    def _metadata(self, result: ResourceAgentResult) -> dict[str, Any]:
        metadata = result.run.model_dump(mode="json")
        metadata.pop("final_report", None)
        metadata.update(
            {
                "workspace_version": WORKSPACE_VERSION,
                "requires_approval": result.requires_approval,
                "compact": {
                    "report_context": self._has_report_context(result),
                    "llm_calls_summary": True,
                },
                "report": self._report_metadata(result.final_report, result.run.ended_at),
                "counts": {
                    "steps": len(result.steps),
                    "tool_results": len(result.tool_results),
                    "evidence_items": len(result.evidence_items),
                    "findings": len(result.findings),
                    "approvals": len(result.approvals),
                    "todos": len(result.todos),
                    "action_results": 0,
                },
            }
        )
        return metadata

    def _metadata_from_snapshot(self, snapshot: DiagnosisSnapshot) -> dict[str, Any]:
        metadata = snapshot.run.model_dump(mode="json")
        metadata.pop("final_report", None)
        metadata.update(
            {
                "workspace_version": WORKSPACE_VERSION,
                "requires_approval": snapshot.requires_approval,
                "report_status": "generating",
                "compact": {
                    "report_context": False,
                    "llm_calls_summary": True,
                },
                "report": {
                    "path": "report.md",
                    "generated_at": None,
                    "snapshot_stage": "diagnosis_pending_report",
                    "sha256": None,
                },
                "counts": {
                    "steps": len(snapshot.steps),
                    "tool_results": len(snapshot.tool_results),
                    "evidence_items": len(snapshot.evidence_items),
                    "findings": len(snapshot.findings),
                    "approvals": len(snapshot.approvals),
                    "todos": len(snapshot.todos),
                    "action_results": 0,
                },
            }
        )
        return metadata

    def _metadata_from_trace(self, trace: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        run = dict(trace["run"])
        final_report = run.pop("final_report", None)
        report_context_path = run_dir / "compact" / "report_context.json"
        report_context = read_json_file(report_context_path) if report_context_path.exists() else None
        run.update(
            {
                "workspace_version": WORKSPACE_VERSION,
                "requires_approval": any(
                    str(approval.get("status")) == "pending"
                    for approval in trace.get("approvals") or []
                ),
                "compact": {
                    "report_context": bool(
                        isinstance(report_context, dict) and report_context.get("available") is True
                    ),
                    "llm_calls_summary": (run_dir / "compact" / "llm_calls_summary.json").exists(),
                },
                "report": self._report_metadata(
                    final_report or (run_dir / "report.md").read_text(encoding="utf-8"),
                    run.get("ended_at"),
                ),
                "counts": {
                    "steps": len(trace.get("steps") or []),
                    "tool_results": len(trace.get("tool_calls") or []),
                    "evidence_items": len(trace.get("evidence_items") or []),
                    "findings": len(trace.get("findings") or []),
                    "approvals": len(trace.get("approvals") or []),
                    "todos": len(trace.get("todos") or []),
                    "action_results": len(trace.get("action_results") or [])
                },
            }
        )
        return run

    def _tool_plan(self, result: ResourceAgentResult) -> dict[str, Any]:
        if result.tool_plan is None:
            return {"available": False, "reason": "tool_plan is not available"}
        return result.tool_plan.model_dump(mode="json")

    def _tool_plan_from_snapshot(self, snapshot: DiagnosisSnapshot) -> dict[str, Any]:
        if snapshot.tool_plan is None:
            return {"available": False, "reason": "tool_plan is not available"}
        return snapshot.tool_plan.model_dump(mode="json")

    def _has_report_context(self, result: ResourceAgentResult) -> bool:
        return self._find_report_context(result) is not None

    def _report_context(self, result: ResourceAgentResult) -> dict[str, Any]:
        context = self._find_report_context(result)
        if context is None:
            return {
                "available": False,
                "reason": "build_report_context step was not produced",
                "report_mode": result.run.report_mode,
            }

        return {
            "available": True,
            "source": "diagnosis_step.observation",
            "step_action": "build_report_context",
            "context": context,
        }

    def _find_report_context(self, result: ResourceAgentResult) -> dict[str, Any] | None:
        for step in result.steps:
            if step.action != "build_report_context":
                continue
            if isinstance(step.observation, dict):
                return self._jsonable(step.observation)
        return None

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    def _write_jsonl(self, path: Path, rows: list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(self._jsonable(row), ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    def _tar_filter(self, info: tarfile.TarInfo, *, include_llm_payloads: bool = False) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        blocked_names = {".env", "approvals.jsonl"}
        if any(part in blocked_names for part in parts):
            return None
        if not include_llm_payloads and info.name.endswith("raw/llm_calls.jsonl"):
            return None
        return info

    def _report_metadata(self, report: str, generated_at: Any) -> dict[str, Any]:
        return {
            "path": "report.md",
            "generated_at": self._jsonable(generated_at),
            "snapshot_stage": "diagnosis",
            "sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        }

    def _trace_from_result(self, result: ResourceAgentResult) -> dict[str, Any]:
        tool_calls = []
        tool_steps = {
            step.action: step for step in result.steps if step.action and step.action not in {"llm_planner", "llm_report"}
        }
        for tool_result in result.tool_results:
            payload = self._jsonable(tool_result)
            payload["step_id"] = getattr(tool_steps.get(tool_result.tool_name), "step_id", None)
            tool_calls.append(payload)
        return {
            "run": self._jsonable(result.run),
            "steps": [self._jsonable(step) for step in result.steps],
            "tool_calls": tool_calls,
            "evidence_items": [self._jsonable(item) for item in result.evidence_items],
            "findings": [self._jsonable(item) for item in result.findings],
            "approvals": [self._jsonable(item) for item in result.approvals],
            "todos": [self._jsonable(item) for item in result.todos],
            "action_results": [],
        }

    def _trace_from_snapshot(self, snapshot: DiagnosisSnapshot) -> dict[str, Any]:
        tool_calls = []
        tool_steps = {
            step.action: step for step in snapshot.steps if step.action and step.action not in {"llm_planner", "llm_report"}
        }
        for tool_result in snapshot.tool_results:
            payload = self._jsonable(tool_result)
            payload["step_id"] = getattr(tool_steps.get(tool_result.tool_name), "step_id", None)
            tool_calls.append(payload)
        return {
            "run": self._jsonable(snapshot.run),
            "steps": [self._jsonable(step) for step in snapshot.steps],
            "tool_calls": tool_calls,
            "evidence_items": [self._jsonable(item) for item in snapshot.evidence_items],
            "findings": [self._jsonable(item) for item in snapshot.findings],
            "approvals": [self._jsonable(item) for item in snapshot.approvals],
            "todos": [self._jsonable(item) for item in snapshot.todos],
            "action_results": [],
        }

    def _compact_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        tool_line = 0
        for step in steps:
            action = step.get("action")
            observation = step.get("observation") or {}
            item = {
                key: step.get(key)
                for key in (
                    "step_id",
                    "run_id",
                    "step_index",
                    "action",
                    "status",
                    "latency_ms",
                    "args",
                    "observation_preview",
                    "error",
                    "created_at",
                )
            }
            if action == "llm_planner":
                item["observation"] = {
                    key: observation.get(key)
                    for key in (
                        "source",
                        "status",
                        "used_llm_plan",
                        "fallback_reason",
                        "prompt_length",
                        "response_length",
                        "response_preview",
                        "validation_errors",
                        "latency_ms",
                    )
                }
                item["selected_plan_ref"] = "../plan.json"
                item["llm_call_ref"] = self._llm_call_ref(observation)
            elif action == "build_tool_plan":
                plan = observation.get("tool_plan") or {}
                item["observation"] = {
                    "planner_mode": plan.get("planner_mode"),
                    "plan_id": plan.get("plan_id"),
                    "step_count": len(plan.get("steps") or []),
                    "selected_tool_names": [entry.get("tool_name") for entry in plan.get("steps") or []],
                }
                item["artifact_ref"] = "../plan.json"
            elif action == "build_report_context":
                item["observation"] = {
                    "context_version": observation.get("context_version"),
                    "counts": {
                        "root_causes": len((observation.get("diagnosis") or {}).get("root_causes") or []),
                        "key_evidence": len(observation.get("key_evidence") or []),
                        "recommendations": len(observation.get("recommendations") or []),
                    },
                    "serialized_chars": len(json.dumps(observation, ensure_ascii=False)),
                }
                item["artifact_ref"] = "../compact/report_context.json"
            elif action == "llm_report":
                item["observation"] = {
                    key: observation.get(key)
                    for key in (
                        "source",
                        "status",
                        "fallback_reason",
                        "prompt_length",
                        "response_length",
                        "response_preview",
                        "latency_ms",
                    )
                }
                item["llm_call_ref"] = self._llm_call_ref(observation)
            elif isinstance(observation, dict) and "tool_name" in observation:
                tool_line += 1
                item["observation"] = {
                    "tool_name": observation.get("tool_name"),
                    "status": observation.get("status"),
                    "preview": observation.get("preview"),
                }
                item["artifact_ref"] = f"../raw/tool_outputs.jsonl#line={tool_line}"
            else:
                item["observation"] = self._small_observation(observation)
            compact.append(item)
        return compact

    def _small_observation(self, observation: Any) -> Any:
        if not isinstance(observation, dict):
            return observation
        return {key: value for key, value in observation.items() if isinstance(value, (str, int, float, bool)) or value is None}

    def _llm_call_ref(self, observation: dict[str, Any]) -> str | None:
        record = observation.get("llm_call") or {}
        call_id = record.get("call_id")
        return f"../compact/llm_calls_summary.json#{call_id}" if call_id else None

    def _write_llm_call_files(self, run_dir: Path, steps: list[dict[str, Any]]) -> None:
        records = extract_llm_calls(steps)
        self._write_json(
            run_dir / "compact" / "llm_calls_summary.json",
            {"summary_version": "v1", "calls": [public_llm_call(record) for record in records]},
        )
        full_records = [record for record in records if record.get("full_payload_stored")]
        raw_path = run_dir / "raw" / "llm_calls.jsonl"
        if full_records:
            self._write_jsonl(raw_path, full_records)
        elif raw_path.exists():
            raw_path.unlink()

    def _write_summary_files(self, run_dir: Path, summary: dict[str, Any]) -> None:
        self._write_json(run_dir / "summary" / "run_summary.json", summary)
        self._write_text(run_dir / "summary" / "run_summary.md", render_run_summary_markdown(summary))

    def _write_remediation_summary(self, run_dir: Path, trace: dict[str, Any]) -> None:
        approvals = trace.get("approvals") or []
        action_results = trace.get("action_results") or []
        rejected = [item for item in approvals if item.get("status") == "rejected"]
        path = run_dir / "remediation_summary.md"
        if not action_results and not rejected:
            if path.exists():
                path.unlink()
            return

        lines = ["# Remediation Summary", ""]
        for approval in rejected:
            lines.extend(
                [
                    f"## Approval {approval.get('approval_id')}",
                    f"- Action: {approval.get('action')}",
                    "- Status: rejected",
                    f"- Decided at: {approval.get('decided_at')}",
                    "",
                ]
            )
        for result in action_results:
            lines.extend(
                [
                    f"## Action {result.get('action_result_id') or result.get('approval_id')}",
                    f"- Approval: {result.get('approval_id')}",
                    f"- Action: {result.get('action')}",
                    f"- Mode: {result.get('mode')}",
                    f"- Status: {result.get('status')}",
                    f"- Changed system state: {bool((result.get('execution') or {}).get('changed_system_state'))}",
                    f"- Preview: {result.get('preview')}",
                    f"- Pre-check: {(result.get('pre_check') or {}).get('passed')}",
                    f"- Post-check: {(result.get('post_check') or {}).get('passed')}",
                    f"- Executed at: {result.get('created_at')}",
                    "",
                ]
            )
        self._write_text(path, "\n".join(lines))

    def _jsonable(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [self._jsonable(item) for item in value]
        return value


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
