from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import Field

from agent.resource_agent import ResourceAgent
from approval.service import ApprovalService
from approval.store import ApprovalStore
from approval.trace_sync import sync_approval_trace
from app.schemas import IncidentSource, ResourceIncident, ResourceType, Severity, StrictBaseModel
from trace.store import TraceStore

from workspace.writer import WorkspaceWriter

app = FastAPI(title="ResourceOps Agent", version="0.1.0")


class DiagnoseRequest(StrictBaseModel):
    #描述故障，必填、长度至少为1   ...表示必填
    #资源类型，可以空，让agent自己判断
    #严重等级：
    #故障发生的主机：可空
    #agent模式：默认是规则型，也可以让 LLM 只写报告或只提出工具计划
    description: str = Field(..., min_length=1)
    resource_type: ResourceType | None = None
    severity: Severity = Severity.WARNING
    host: str | None = None
    agent_mode: str | None = Field(default=None, pattern="^(deterministic|llm_report|llm_planner|llm_full)$")
    planner_mode: str | None = Field(default=None, pattern="^(deterministic|llm)$")
    report_mode: str | None = Field(default=None, pattern="^(template|llm)$")

#get接口，访问curl http://localhost:8000/health返回status：ok

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

def write_workspace_result(result) -> None:
    try:
        WorkspaceWriter().write_agent_result(result)
    except OSError:
        return

def sync_workspace_from_trace(run_id: str, trace_store: TraceStore) -> None:
    try:
        WorkspaceWriter().update_from_trace(run_id, trace_store)
    except FileNotFoundError:
        return
    except OSError:
        return

def build_trace_store() -> TraceStore:
    return TraceStore()


def build_approval_store() -> ApprovalStore:
    return ApprovalStore()


def build_approval_service() -> ApprovalService:
    return ApprovalService(store=build_approval_store())

def build_resource_agent(
    approval_service: ApprovalService,
    agent_mode: str | None = None,
    planner_mode: str | None = None,
    report_mode: str | None = None,
) -> ResourceAgent:
    return ResourceAgent(
        approval_service=approval_service,
        agent_mode=agent_mode,
        planner_mode=planner_mode,
        report_mode=report_mode,
    )
#提交诊断请求，
@app.post("/diagnose")
def diagnose(request: DiagnoseRequest) -> dict[str, Any]:
    #保存agent的运行结果和过程记录
    trace_store = build_trace_store()
    approval_service = build_approval_service()
    incident = ResourceIncident(
        #把外部api请求转换为整个内部使用的ResourceIncident
        #多了一个字段：source：cli、api、scheduled、scheduled
        description=request.description,
        resource_type=request.resource_type,
        severity=request.severity,
        source=IncidentSource.API,
        host=request.host,
    )
    result = build_resource_agent(
        approval_service=approval_service,
        agent_mode=request.agent_mode,
        planner_mode=request.planner_mode,
        report_mode=request.report_mode,
    ).diagnose(incident)
    trace_store.save_agent_result(result)
    write_workspace_result(result)
    return result.model_dump(mode="json")

#返回最近agent的运行记录
@app.get("/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return build_trace_store().list_runs(limit=limit)

#根据run_id查询某次完整的诊断过程的接口
@app.get("/runs/{run_id}")
def get_run_trace(run_id: str) -> dict[str, Any]:
    try:
        return build_trace_store().get_trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

#查看审批表，默认只查看pending的
@app.get("/approvals")
def list_approvals(status: str | None = Query(default="pending")) -> list[dict[str, Any]]:
    normalized = status.strip() if status else None
    return [approval.model_dump(mode="json") for approval in build_approval_store().list(status=normalized)]

#批准审批
#审批通过后，执行并返回工具执行结果
#如果已经拒绝或已经执行过则返回400
@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str) -> dict[str, Any]:
    trace_store = build_trace_store()
    approval_store = build_approval_store()
    service = ApprovalService(store=approval_store)
    try:
        approval, tool_result = service.approve(approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sync_approval_trace(trace_store, approval_store, approval)
    sync_workspace_from_trace(approval.run_id, trace_store)
    return {"approval": approval.model_dump(mode="json"), "tool_result": tool_result.model_dump(mode="json")}

#拒绝
@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: str) -> dict[str, Any]:
    trace_store = build_trace_store()
    approval_store = build_approval_store()
    service = ApprovalService(store=approval_store)
    try:
        approval = service.reject(approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sync_approval_trace(trace_store, approval_store, approval)
    sync_workspace_from_trace(approval.run_id, trace_store)
    return approval.model_dump(mode="json")
