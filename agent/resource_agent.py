from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.detectors import run_detectors
from agent.llm_client import LlmClient, build_default_llm_client_from_env
from agent.llm_planner import build_llm_tool_plan_result
from agent.llm_report import build_llm_report_result
from agent.plan_validator import PlanValidator
from agent.planner import build_tool_plan, infer_resource_type, tool_plan_preview
from agent.report_context import build_report_context, report_context_preview
from agent.report import build_p4_report
from agent.tool_catalog import build_tool_catalog
from approval.service import ApprovalService
from app.schemas import (
    AgentPlannerMode,
    DiagnosisFinding,
    DiagnosisRun,
    DiagnosisStep,
    EvidenceItem,
    Recommendation,
    ReportMode,
    ResourceAgentResult,
    ResourceIncident,
    RunStatus,
    TodoDisplayGroup,
    utc_now,
)
from tools.registry import ToolExecutionResult, ToolRegistry, default_registry
from agent.todos import (
    build_phase_todos,
    mark_todo_completed,
    mark_todo_failed,
    mark_todo_running,
    mark_todo_skipped,
    mark_todo_waiting_approval,
    phase_by_group,
    todos_from_tool_plan,
    todos_from_approvals,
)
from agent.events import AgentEventSink, NoopAgentEventSink

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
    build_tool_catalog(registry)
      ↓
    build_tool_plan(resource_type, catalog) 或 llm_planner + PlanValidator
      ↓
    ToolRegistry.execute(tool_name, args)
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
        planner_mode: str | AgentPlannerMode | None = None,
        report_mode: str | ReportMode | None = None,
        workspace_root: Path | str | None = None,
        llm_client: LlmClient | None = None,
        event_sink: AgentEventSink | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.approval_service = approval_service
        self.agent_mode, self.planner_mode, self.report_mode = resolve_agent_modes(
            agent_mode=agent_mode,
            planner_mode=planner_mode,
            report_mode=report_mode,
        )
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[1] / "var" / "runs")
        self.llm_client = llm_client or build_default_llm_client_from_env()
        self.event_sink = event_sink or NoopAgentEventSink()

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
            planner_mode=self.planner_mode,
            report_mode=self.report_mode
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
        
        phases = build_phase_todos(run.run_id)
        tool_todos = []
        approval_todos = []
        self.event_sink.on_phase_snapshot(phases)

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.PLANNING)
        phases[phase_index] = mark_todo_running(phase)
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        catalog_registry = self._catalog_registry()
        tool_catalog = build_tool_catalog(catalog_registry)
        deterministic_plan = build_tool_plan(
            resource_type=resource_type,
            user_question=incident.description,
            tool_catalog=tool_catalog,
        )
        tool_plan = deterministic_plan

        if self.planner_mode == AgentPlannerMode.LLM:
            llm_plan_result = build_llm_tool_plan_result(
                description=incident.description,
                resource_type=resource_type,
                tool_catalog=tool_catalog,
                fallback_plan=deterministic_plan,
                validator=PlanValidator(
                    tool_catalog=tool_catalog,
                    registry=catalog_registry,
                    max_steps=8,
                ),
                llm_client=self.llm_client,
            )
            tool_plan = llm_plan_result.tool_plan
            steps.append(
                DiagnosisStep(
                    run_id=run.run_id,
                    step_index=len(steps),
                    thought="Ask the LLM planner for a candidate tool plan and validate it before execution.",
                    action="llm_planner",
                    args={
                        "agent_mode": self.agent_mode,
                        "planner_mode":self.planner_mode,
                        "report_mode": self.report_mode,
                        "resource_type": resource_type.value,
                        "tool_catalog_version": tool_catalog.catalog_version,
                    },
                    observation=llm_plan_result.model_dump(),
                    observation_preview=llm_plan_result.preview,
                    latency_ms=llm_plan_result.latency_ms,
                    error=llm_plan_result.error,
                )
            )

        steps.append(
            DiagnosisStep(
                run_id=run.run_id,
                step_index=len(steps),
                thought="Select the structured tool plan that will be executed.",
                action="build_tool_plan",
                args={
                    "planner_mode": tool_plan.planner_mode,
                    "resource_type": resource_type.value,
                    "tool_catalog_version": tool_catalog.catalog_version,
                },
                observation={
                    "tool_plan": tool_plan.model_dump(mode="json"),
                    "tool_catalog": tool_catalog.model_dump(mode="json"),
                },
                observation_preview=tool_plan_preview(tool_plan),
                latency_ms=0,
            )
        )

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.PLANNING)
        phases[phase_index] = mark_todo_completed(phase, "tool plan ready")
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.TOOLS)
        phases[phase_index] = mark_todo_running(phase)
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        tool_todos = todos_from_tool_plan(
            run_id=run.run_id,
            tool_plan=tool_plan,
            parent_todo_id=phases[phase_index].todo_id,
        )
        self.event_sink.on_todo_snapshot(tool_todos)

        tool_results: list[ToolExecutionResult] = []

        for index, planned in enumerate(tool_plan.steps):
            tool_todos[index] = mark_todo_running(tool_todos[index])
            self.event_sink.on_todo_updated(tool_todos[index], tool_todos)

            result = self.registry.execute(planned.tool_name, planned.args)
            tool_results.append(result)

            if result.error:
                tool_todos[index] = mark_todo_failed(tool_todos[index], result.error)
            else:
                tool_todos[index] = mark_todo_completed(tool_todos[index], result.preview)
            
            self.event_sink.on_todo_updated(tool_todos[index], tool_todos)

            steps.append(
                DiagnosisStep(
                    run_id=run.run_id,
                    step_index=len(steps),
                    thought=planned.reason,
                    action=planned.tool_name,
                    args=planned.args,
                    observation=result.model_dump(mode="json"),
                    observation_preview=result.preview,
                    latency_ms=result.latency_ms,
                    error=result.error,
                )
            )

        failed_tool_count = sum(1 for result in tool_results if result.error)
        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.TOOLS)
        if failed_tool_count:
            phases[phase_index] = mark_todo_failed(phase, f"{failed_tool_count} tool error(s)")
        else:
            phases[phase_index] = mark_todo_completed(phase, f"{len(tool_results)} tools executed")
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        evidence_items, findings = run_detectors(run.run_id, tool_results)
        requires_approval = any(finding.requires_approval for finding in findings)
        approvals = self._create_approvals(run.run_id, findings) if requires_approval else []
        approval_phase_index, approval_phase = phase_by_group(phases, TodoDisplayGroup.APPROVAL)
        approval_todos = todos_from_approvals(
            run_id=run.run_id,
            approvals=approvals,
            parent_todo_id=approval_phase.todo_id,
            start_index=len(tool_todos),
            )
        if approval_todos:
            self.event_sink.on_todo_snapshot(tool_todos + approval_todos)

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.REPORT)
        phases[phase_index] = mark_todo_running(phase)
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        deterministic_report = build_p4_report(
            description=incident.description,
            resource_type=resource_type,
            steps=steps,
            tool_results=tool_results,
            evidence_items=evidence_items,
            findings=findings,
            approvals=approvals,
        )
        final_report = deterministic_report
        if self.report_mode == ReportMode.LLM:
            report_context = build_report_context(
                description=incident.description,
                resource_type=resource_type,
                tool_results=tool_results,
                evidence_items=evidence_items,
                findings=findings,
                approvals=approvals,
            )
            steps.append(
                DiagnosisStep(
                    run_id=run.run_id,
                    step_index=len(steps),
                    thought="Build a bounded, redacted report context from tool results for LLM report writing.",
                    action="build_report_context",
                    args={
                        "context_version": report_context["context_version"],
                        "resource_type": resource_type.value,
                        "serialized_chars": len(json.dumps(report_context, ensure_ascii=False)),
                    },
                    observation=report_context,
                    observation_preview=report_context_preview(report_context),
                    latency_ms=0,
                )
            )
            llm_report_result = build_llm_report_result(
                deterministic_report=deterministic_report,
                description=incident.description,
                resource_type=resource_type,
                tool_results=tool_results,
                evidence_items=evidence_items,
                findings=findings,
                approvals=approvals,
                llm_client=self.llm_client,
                report_context=report_context,
            )
            final_report = llm_report_result.final_report
            steps.append(
                DiagnosisStep(
                    run_id=run.run_id,
                    step_index=len(steps),
                    thought="Rewrite the deterministic diagnosis report with an LLM using only existing evidence, findings, recommendations, and approvals.",
                    action="llm_report",
                    args={
                        "agent_mode": self.agent_mode,
                        "planner_mode": self.planner_mode,
                        "report_mode": self.report_mode,
                        "resource_type": resource_type.value,
                    },
                    observation=llm_report_result.model_dump(),
                    observation_preview=llm_report_result.preview,
                    latency_ms=llm_report_result.latency_ms,
                    error=llm_report_result.error,
                )
            )

        report_preview = "report ready"
        if self.report_mode == ReportMode.LLM:
            report_preview = steps[-1].observation_preview or report_preview

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.REPORT)
        phases[phase_index] = mark_todo_completed(phase, report_preview)
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.APPROVAL)
        if approvals:
            phases[phase_index] = mark_todo_waiting_approval(phase, f"{len(approvals)} approval(s) pending")
        else:
            phases[phase_index] = mark_todo_completed(phase, "no approvals")
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        phase_index, phase = phase_by_group(phases, TodoDisplayGroup.ACTIONS)
        phases[phase_index] = mark_todo_skipped(phase, "reserved for action executor")
        self.event_sink.on_phase_updated(phases[phase_index], phases)

        run.status = RunStatus.WAITING_APPROVAL if approvals else RunStatus.COMPLETED
        run.final_report = final_report
        run.root_cause = summarize_root_cause(findings)
        run.summary = (
            f"Executed {count_phrase(len(tool_results), 'resource tool')} for {resource_type.value}; "
            f"detected {count_phrase(len(findings), 'finding')}, "
            f"{count_phrase(len(evidence_items), 'evidence item')}, "
            f"and {count_phrase(len(approvals), 'approval')}."
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
            tool_plan=tool_plan,
            steps=steps,
            tool_results=tool_results,
            evidence_items=evidence_items,
            findings=findings,
            final_report=final_report,
            requires_approval=bool(approvals),
            approvals=approvals,
            todos=phases + tool_todos + approval_todos,
        )

    def _prepare_workspace(self, run_id: str) -> None:
        for dirname in ("raw", "compact"):
            (self.workspace_root / run_id / dirname).mkdir(parents=True, exist_ok=True)

    def _catalog_registry(self) -> ToolRegistry:
        """返回能导出工具目录的 registry。

        测试里有些 fixture registry 只实现 execute()，不实现 list_tools()。
        P8 的工具目录仍然使用默认工具说明，执行时继续使用传入的 fixture registry。
        """

        list_tools = getattr(self.registry, "list_tools", None)
        if callable(list_tools):
            return self.registry
        return default_registry()

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

def count_phrase(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"

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

def resolve_agent_modes(
    agent_mode: str | None = None,
    planner_mode: str | AgentPlannerMode | None = None,
    report_mode: str | ReportMode | None = None,
) -> tuple[str, AgentPlannerMode, ReportMode]:
    """把旧 agent_mode 和新 planner/report mode 统一成内部模式。

    兼容规则：
    - deterministic -> deterministic planner + template report
    - llm_report -> deterministic planner + llm report
    - llm_planner -> llm planner + template report
    - llm_full -> llm planner + llm report
    """

    legacy_map = {
        None: (AgentPlannerMode.DETERMINISTIC, ReportMode.TEMPLATE),
        "deterministic": (AgentPlannerMode.DETERMINISTIC, ReportMode.TEMPLATE),
        "llm_report": (AgentPlannerMode.DETERMINISTIC, ReportMode.LLM),
        "llm_planner": (AgentPlannerMode.LLM, ReportMode.TEMPLATE),
        "llm_full": (AgentPlannerMode.LLM, ReportMode.LLM),
    }

    if agent_mode not in legacy_map:
        raise ValueError(f"unsupported agent_mode: {agent_mode}")

    legacy_planner_mode, legacy_report_mode = legacy_map[agent_mode]
    resolved_planner_mode = AgentPlannerMode(planner_mode) if planner_mode is not None else legacy_planner_mode
    resolved_report_mode = ReportMode(report_mode) if report_mode is not None else legacy_report_mode

    return (
        compose_agent_mode(resolved_planner_mode, resolved_report_mode),
        resolved_planner_mode,
        resolved_report_mode,
    )


def compose_agent_mode(planner_mode: AgentPlannerMode, report_mode: ReportMode) -> str:
    if planner_mode == AgentPlannerMode.DETERMINISTIC and report_mode == ReportMode.TEMPLATE:
        return "deterministic"
    if planner_mode == AgentPlannerMode.DETERMINISTIC and report_mode == ReportMode.LLM:
        return "llm_report"
    if planner_mode == AgentPlannerMode.LLM and report_mode == ReportMode.TEMPLATE:
        return "llm_planner"
    if planner_mode == AgentPlannerMode.LLM and report_mode == ReportMode.LLM:
        return "llm_full"
    raise ValueError(f"unsupported mode combination: planner={planner_mode}, report={report_mode}")
