"""Core schemas for ResourceOps Agent.

The P0 layer defines the durable contract for resource diagnosis runs. It keeps
the IncidentOps harness shape, but renames the domain around GPU/CPU/Memory
resource diagnosis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ResourceType(str, Enum):
    GPU = "gpu"
    CPU = "cpu"
    MEMORY = "memory"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentSource(str, Enum):
    CLI = "cli"
    API = "api"
    SCHEDULED = "scheduled"
    BACKGROUND = "scheduled"
    #定时任务触发的scheduled

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolPermissionLevel(str, Enum):
    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"


class ToolCallStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED_FOR_APPROVAL = "blocked_for_approval"


class EvidenceCategory(str, Enum):
    GPU = "gpu"
    CPU = "cpu"
    MEMORY = "memory"
    PROCESS = "process"
    SYSTEM = "system"
    OOM = "oom"
    SKILL = "skill"


class EvidenceLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RiskLevel(str, Enum):
    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ResourceIncident(StrictBaseModel):
    """标准的一次资源故障输入"""
    incident_id: str = Field(default_factory=lambda: new_id("rinc"))
    description: str = Field(..., min_length=1)
    resource_type: ResourceType | None = None
    severity: Severity = Severity.WARNING
    source: IncidentSource = IncidentSource.CLI
    created_at: datetime = Field(default_factory=utc_now)
    host: str | None = None

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("description must not be empty")
        return normalized

    @field_validator("host")
    @classmethod
    def strip_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DiagnosisRun(StrictBaseModel):
    """agent 对某个resourceincident做的一次完整诊断"""
    run_id: str = Field(default_factory=lambda: new_id("run"))
    incident_id: str
    status: RunStatus = RunStatus.PENDING
    user_input: str
    resource_type: ResourceType = ResourceType.MIXED
    agent_mode: str = "deterministic"
    final_report: str | None = None
    root_cause: str | None = None
    summary: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    error: str | None = None


class DiagnosisStep(StrictBaseModel):
    """agent诊断过程中的一步
    例如：1.识别问题类型，2.查询CPU状态，3.查询进程列表，4.生成结论"""
    step_id: str = Field(default_factory=lambda: new_id("step"))
    run_id: str
    step_index: int = Field(..., ge=0)
    thought: str
    action: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    observation: Any | None = None
    observation_preview: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    status: StepStatus = StepStatus.COMPLETED
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ToolCall(StrictBaseModel):
    """记录一次工具调用，用于审计和复盘"""
    call_id: str = Field(default_factory=lambda: new_id("call"))
    run_id: str
    step_id: str | None = None
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    preview: str | None = None
    summary: str | None = None
    permission_level: ToolPermissionLevel = ToolPermissionLevel.SAFE
    latency_ms: int | None = Field(default=None, ge=0)
    status: ToolCallStatus = ToolCallStatus.SUCCESS
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class EvidenceItem(StrictBaseModel):
    """agent在诊断过程中收集到的一条证据
    例如：GPU0 显存使用率 96%
        python 进程占用 22GB 显存
        系统日志出现 CUDA out of memory"""
    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    run_id: str
    source_tool: str
    category: EvidenceCategory
    level: EvidenceLevel = EvidenceLevel.INFO
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class Recommendation(StrictBaseModel):
    """agent给出的建议操作
    📢：默认是需要审批的这里我手动先定为要审批"""
    action: str
    description: str
    risk: RiskLevel = RiskLevel.SAFE
    requires_approval: bool = True
    command_preview: str | None = None
    reason: str


class DiagnosisFinding(StrictBaseModel):
    """agent 的一个诊断结论，一个诊断运行可以有多个finding"""
    finding_id: str = Field(default_factory=lambda: new_id("find"))
    run_id: str
    finding_type: str
    title: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_actions: list[Recommendation] = Field(default_factory=list)
    requires_approval: bool = False


class Approval(StrictBaseModel):
    """表示等待人工确认的危险操作"""
    approval_id: str = Field(default_factory=lambda: new_id("appr"))
    run_id: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str
    risk: RiskLevel = RiskLevel.DANGEROUS
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    executed_at: datetime | None = None
