from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.planner import infer_resource_type,build_plan
from agent.detectors import run_detectors
from agent.report import build_p4_report
from approval.service import ApprovalService
from app.schemas import (
    DiagnosisFinding,
    DiagnosisRun,
    DiagnosisStep,
    EvidenceItem,
    Recommendation,
    ResourceIncident,
    RiskLevel,
    RunStatus,
    utc_now,
)
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
    """ResourceOps V1-P4 主 Agent。

    当前流程：
    用户通过 CLI/API 输入问题
      ↓
    构造 ResourceIncident
      ↓
    ResourceAgent.diagnose(incident)
      ↓
    infer_resource_type()
      ↓
    创建 DiagnosisRun 和 run workspace
      ↓
    build_plan(resource_type)
      ↓
    ToolRegistry.execute(action, args)
      ↓
    生成 DiagnosisStep 和 ToolExecutionResult
      ↓
    run_detectors(tool_results)
      ↓
    生成 EvidenceItem 和 DiagnosisFinding
      ↓
    build_p4_report()
      ↓
    为危险建议创建 Approval
      ↓
    返回 ResourceAgentResult，交给 CLI/API 保存 trace 并输出报告
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


        steps : list[DiagnosisStep] = [
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

        tool_results: list[ToolExecutionResult] = []
        planned_actions = build_plan(resource_type)

        for planned in planned_actions:
            result = self.registry.execute(planned.action, planned.args)
            tool_results.append(result)

            steps.append(
                DiagnosisStep(
                    run_id=run.run_id,
                    step_index=len(steps),
                    thought=planned.thought,
                    action=planned.action,
                    args=planned.args,
                    observation=result.model_dump(mode="json"),
                    observation_preview=result.preview,
                    latency_ms=result.latency_ms,
                    error=result.error,
                )
            )

        evidence_items, findings = run_detectors(run.run_id, tool_results)
        requires_approval = any(finding.requires_approval for finding in findings)
        approvals = self._create_approvals(run.run_id, findings) if requires_approval else []

        final_report = build_p4_report(
            description=incident.description,
            resource_type=resource_type,
            steps=steps,
            tool_results=tool_results,
            evidence_items=evidence_items,
            findings=findings,
            approvals=approvals,
        )
        run.status = RunStatus.WAITING_APPROVAL if approvals else RunStatus.COMPLETED
        run.final_report = final_report
        run.root_cause = summarize_root_cause(findings)
        run.summary = (
            f"Executed {len(tool_results)} resource tools for {resource_type.value}; "
            f"detected {len(findings)} findings and {len(evidence_items)} evidence items."
            f"and {len(approvals)} approvals."
        )
        run.ended_at = utc_now()

        """
        finding.requires_approval=True
        ↓
        _create_approvals()
        ↓
        有 approvals
        ↓
        run.status = waiting_approval
        """

        return ResourceAgentResult(
            run=run,
            steps=steps,
            tool_results=tool_results,
            evidence_items=evidence_items,
            findings=findings,
            final_report=final_report,
            requires_approval=bool(approvals),
            approvals=approvals,
        )

    def _prepare_workspace(self, run_id: str) -> None:
        for dirname in ("raw", "compact"):
            (self.workspace_root / run_id / dirname).mkdir(parents=True, exist_ok=True)

    def _create_approvals(
        self,
        run_id: str,
        findings: list[DiagnosisFinding],
    ) -> list[dict[str, Any]]:
        if self.approval_service is None:
            return []

        approvals: list[dict[str, Any]] = []
        for finding in findings:
            for action in finding.recommended_actions:
                if not action.requires_approval:
                    continue

                approval = self.approval_service.request_approval(
                    run_id=run_id,
                    action=action.action,
                    args=approval_args_from_recommendation(action),
                    reason=action.reason,
                    risk=action.risk,
                )
                approvals.append(approval.model_dump(mode="json"))

        return approvals


def summarize_root_cause(findings: list[DiagnosisFinding]) -> str:
    if not findings:
        return "no detector findings matched current thresholds"
    return "; ".join(finding.finding_type for finding in findings[:3])

def approval_args_from_recommendation(action: Recommendation) -> dict[str, Any]:
    args: dict[str, Any] = {}

    if action.action == "kill_process" and action.command_preview:
        parts = action.command_preview.strip().split()
        if len(parts) >= 2 and parts[0] == "kill":
            try:
                args["pid"] = int(parts[1])
            except ValueError:
                pass

    if action.command_preview:
        args["command_preview"] = action.command_preview

    return args
