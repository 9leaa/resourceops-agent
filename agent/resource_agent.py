from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.planner import infer_resource_type,build_plan
from agent.report import build_p2_report
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
    """用户通过 CLI/API 输入问题
  ↓
构造 ResourceIncident
  ↓
调用 ResourceAgent.diagnose(incident)
  ↓
infer_resource_type()
  判断问题是 gpu / cpu / memory / mixed
  ↓
创建 DiagnosisRun
  status = running
  ↓
创建 run workspace
  var/runs/<run_id>/raw
  var/runs/<run_id>/compact
  ↓
创建第 0 步 DiagnosisStep
  action = infer_resource_type
  ↓
build_plan(resource_type)
  根据类型生成固定工具计划
  ↓
循环执行 planned_actions
  ↓
ToolRegistry.execute(action, args)
  统一校验参数、执行工具、处理错误、返回 ToolExecutionResult
  ↓
每个工具结果生成一个 DiagnosisStep
  thought/action/args/observation/preview/latency/error
  ↓
收集 tool_results
  ↓
build_p2_report()
  生成当前阶段报告
  ↓
run.status = completed
  run.final_report = ...
  run.root_cause = "detectors not implemented in V1-P2"
  run.summary = ...
  run.ended_at = ...
  ↓
返回 ResourceAgentResult
  ↓
CLI/API 调用方保存 trace
  ↓
CLI 打印报告 / API 返回 JSON

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


        steps : list[DiagnosisFinding] = [
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

        final_report = build_p2_report(
            description=incident.description,
            resource_type=resource_type,
            steps=steps,
            tool_results=tool_results,
        )
        run.status = RunStatus.COMPLETED
        run.final_report = final_report
        run.root_cause = "diagnosis not implemented in V1-P2"
        run.summary = f"Executed {len(tool_results)} resource tools for {resource_type.value}; detectors start in V1-P3."
        run.ended_at = utc_now()

        return ResourceAgentResult(
            run=run,
            steps=steps,
            tool_results=tool_results,
            evidence_items=[],
            findings=[],
            final_report=final_report,
            requires_approval=False,
            approvals=[],
        )

    def _prepare_workspace(self, run_id: str) -> None:
        for dirname in ("raw", "compact"):
            (self.workspace_root / run_id / dirname).mkdir(parents=True, exist_ok=True)
