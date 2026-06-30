from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from typing import Any

from app.schemas import ResourceAgentResult


DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[1] / "var" / "runs"
DEFAULT_BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "var" / "bundles"
WORKSPACE_VERSION = "p11.5"


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

        self._write_json(run_dir / "trace" / "steps.json", [self._jsonable(step) for step in result.steps])
        self._write_json(run_dir / "trace" / "evidence.json", [self._jsonable(item) for item in result.evidence_items])
        self._write_json(run_dir / "trace" / "findings.json", [self._jsonable(item) for item in result.findings])
        self._write_json(run_dir / "trace" / "approvals.json", [self._jsonable(item) for item in result.approvals])
        self._write_json(run_dir / "trace" / "action_results.json", [])

        return run_dir

    def update_from_trace(self, run_id: str, trace_store: Any) -> Path:
        run_dir = self.run_dir(run_id)
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {run_dir}")

        trace = trace_store.get_trace(run_id)
        self._write_json(run_dir / "metadata.json", self._metadata_from_trace(trace, run_dir))
        self._write_json(run_dir / "todos.json", trace.get("todos") or [])
        self._write_json(run_dir / "trace" / "approvals.json", trace.get("approvals") or [])
        # P12 approve 后会新增 action_result 和 action todo，这里跟随 trace 刷新。
        self._write_json(run_dir / "trace" / "action_results.json", trace.get("action_results") or [])
        return run_dir

    def create_bundle(
        self,
        run_id: str,
        bundle_root: Path | str | None = None,
    ) -> Path:
        run_dir = self.run_dir(run_id)
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {run_dir}")

        output_root = resolve_bundle_root(bundle_root)
        output_root.mkdir(parents=True, exist_ok=True)
        bundle_path = output_root / f"{run_id}.tar.gz"
        with tarfile.open(bundle_path, "w:gz") as archive:
            archive.add(run_dir, arcname=f"runs/{run_id}", filter=self._tar_filter)
        return bundle_path

    def _metadata(self, result: ResourceAgentResult) -> dict[str, Any]:
        metadata = result.run.model_dump(mode="json")
        metadata.update(
            {
                "workspace_version": WORKSPACE_VERSION,
                "requires_approval": result.requires_approval,
                "compact": {
                    "report_context": self._has_report_context(result),
                },
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

    def _metadata_from_trace(self, trace: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        run = dict(trace["run"])
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
                },
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

    def _tar_filter(self, info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        blocked_names = {".env", "approvals.jsonl"}
        if any(part in blocked_names for part in parts):
            return None
        return info

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
