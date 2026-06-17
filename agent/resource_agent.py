from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.planner import infer_resource_type
from agent.report import build_p0_report
from approval.service import ApprovalService
from app.schemas import DiagnosisFinding, DiagnosisRun, DiagnosisStep, EvidenceItem, ResourceIncident, RunStatus, utc_now
from tools.registry import ToolExecutionResult, ToolRegistry, default_registry


class ResourceAgentResult:
    def __init__(
        self,
        run: DiagnosisRun,
        steps: list[DiagnosisStep],
        tool_results: list[ToolExecutionResult],
        evidence_items: list[EvidenceItem],
        findings: list[DiagnosisFinding],
        final_report: str,
        requires_approval: bool,
        approvals: list[dict[str, Any]],
    ) -> None:
        self.run = run
        self.steps = steps
        self.tool_results = tool_results
        self.evidence_items = evidence_items
        self.findings = findings
        self.final_report = final_report
        self.requires_approval = requires_approval
        self.approvals = approvals

    def model_dump(self) -> dict[str, Any]:
        """把整个结果转为普通字典"""
        return {
            "run": self.run.model_dump(mode="json"),
            "steps": [step.model_dump(mode="json") for step in self.steps],
            "tool_results": [result.model_dump(mode="json") for result in self.tool_results],
            "evidence_items": [item.model_dump(mode="json") for item in self.evidence_items],
            "findings": [finding.model_dump(mode="json") for finding in self.findings],
            "final_report": self.final_report,
            "requires_approval": self.requires_approval,
            "approvals": self.approvals,
        }


class ResourceAgent:
    """V1-P0 ResourceOps Agent.

    P0 creates a traceable diagnosis run and report shell. Real GPU/CPU/Memory
    tools and detectors arrive in P1-P3.
    
    工作流程p0
    ResourceAgent.diagnose()
    ↓
    infer_resource_type()
    ↓
    创建 DiagnosisRun，状态为 running
    ↓
    创建 var/runs/<run_id>/raw 和 compact 目录
    ↓
    创建 DiagnosisStep #0，记录资源类型推断
    ↓
    build_p0_report()
    ↓
    把 run 状态改成 completed
    ↓
    填入 final_report、root_cause、summary、ended_at
    ↓
    返回 ResourceAgentResult

    """



    def __init__(
        self,
        registry: ToolRegistry | None = None,
        approval_service: ApprovalService | None = None,
        agent_mode: str = "deterministic",
        workspace_root: Path | str | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.approval_service = approval_service
        self.agent_mode = agent_mode
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[1] / "var" / "runs")

    def diagnose(self, incident: ResourceIncident) -> ResourceAgentResult:
        """核心诊断方法incident -> agentresult"""
        #infer_resource_type支持用户指定类型
        #resourceops diagnose "GPU 显存占用过高" --resource-type gpu
        resource_type = infer_resource_type(incident.description, incident.resource_type)
        run = DiagnosisRun(
            incident_id=incident.incident_id,
            status=RunStatus.RUNNING,
            user_input=incident.description,
            resource_type=resource_type,
            agent_mode=self.agent_mode,
        )
        self._prepare_workspace(run.run_id)
        #raw：原始工具输出、原始日志
        #compact：压缩后的摘要、轻量trace


        steps = [
            #第一步：先标准化用户请求，并推断资源类型
            DiagnosisStep(
                run_id=run.run_id,
                step_index=0,
                thought="Normalize the resource diagnosis request and infer the target resource scope.",
                action="infer_resource_type",
                args={"description": incident.description, "resource_type": incident.resource_type},
                observation={"resource_type": resource_type.value},
                observation_preview=f"resource_type={resource_type.value}",
                latency_ms=0,
            )
        ]

        final_report = build_p0_report(incident.description, resource_type)
        run.status = RunStatus.COMPLETED
        run.final_report = final_report
        run.root_cause = "diagnosis not implemented in V1-P0"
        run.summary = f"Created ResourceOps diagnosis run for {resource_type.value}; real resource tools start in V1-P1."
        run.ended_at = utc_now()

        return ResourceAgentResult(
            run=run,
            steps=steps,
            tool_results=[],
            evidence_items=[],
            findings=[],
            final_report=final_report,
            requires_approval=False,
            approvals=[],
        )

    def _prepare_workspace(self, run_id: str) -> None:
        for dirname in ("raw", "compact"):
            (self.workspace_root / run_id / dirname).mkdir(parents=True, exist_ok=True)
