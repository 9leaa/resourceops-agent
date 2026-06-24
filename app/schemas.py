"""ResourceOps Agent 的核心数据结构。

这个文件是项目的数据合同层。CLI、API、Agent、Trace、Approval 和测试都依赖
这里的对象来传递结构化数据。读代码时可以把它理解成系统里所有“名词”的定义：

- ResourceIncident：用户输入的一次资源问题。
- DiagnosisRun：Agent 对这个问题做的一次诊断运行。
- DiagnosisStep：Agent 诊断过程中的一步。
- ToolCall：一次工具调用的审计记录。
- EvidenceItem：detector 从工具结果里提取出的一条证据。
- DiagnosisFinding：detector 基于证据形成的诊断发现。
- Recommendation：针对某个发现给出的建议操作。
- Approval：危险操作的人工审批记录。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """返回带时区的 UTC 时间，用于所有持久化记录。"""

    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """生成带类型前缀的短 ID，例如 run_xxx、step_xxx。"""

    return f"{prefix}_{uuid4().hex[:12]}"


class StrictBaseModel(BaseModel):
    """所有 schema 的基类。

    extra="forbid" 表示不允许多余字段。这样做是为了尽早发现数据结构漂移：
    API 请求、工具输出、未来 LLM 生成的 JSON 只要多了未知字段，就会直接报错。
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ResourceType(str, Enum):
    """Agent 要诊断的资源范围。

    GPU：GPU 利用率、GPU 显存、nvidia-smi、GPU 进程。
    CPU：CPU load、CPU 使用率、CPU 高占用进程。
    MEMORY：系统内存、swap、OOM、RSS 高占用进程。
    MIXED：混合资源诊断，常用于“训练很慢”“瓶颈在哪”这类问题。
    UNKNOWN：预留值，未来用于明确不想猜测资源类型的情况。
    """

    GPU = "gpu"
    CPU = "cpu"
    MEMORY = "memory"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    """资源问题的严重程度。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentSource(str, Enum):
    """ResourceIncident 的来源。"""

    CLI = "cli"
    API = "api"
    SCHEDULED = "scheduled"
    BACKGROUND = "background"


class RunStatus(str, Enum):
    """一次诊断运行的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    """一个诊断步骤的状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolPermissionLevel(str, Enum):
    """工具权限等级。

    SAFE：只读采集或检查，不改变系统状态。
    WRITE：会改变本地状态，但一般不是破坏性操作。
    DANGEROUS：危险操作，例如 kill 进程，必须走人工审批。
    """

    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"


class ToolCallStatus(str, Enum):
    """一次工具调用的标准化结果状态。"""

    SUCCESS = "success"
    ERROR = "error"
    BLOCKED_FOR_APPROVAL = "blocked_for_approval"


class EvidenceCategory(str, Enum):
    """证据分类，用于报告和 trace 展示时过滤/分组。"""

    GPU = "gpu"
    CPU = "cpu"
    MEMORY = "memory"
    PROCESS = "process"
    SYSTEM = "system"
    OOM = "oom"
    SKILL = "skill"


class EvidenceLevel(str, Enum):
    """单条证据的严重程度。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RiskLevel(str, Enum):
    """建议操作或审批动作的风险等级。"""

    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"


class ApprovalStatus(str, Enum):
    """人工审批请求的生命周期状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ResourceIncident(StrictBaseModel):
    """标准化后的资源问题输入。

    字段说明：
    - incident_id：资源问题 ID，自动生成，前缀是 rinc。
    - description：用户输入的问题描述，例如“为什么 CPU 很高？”。
    - resource_type：用户显式指定的资源类型；如果不填，由 planner 推断。
    - severity：问题严重程度，目前主要用于展示和未来路由。
    - source：来源，例如 CLI、API、后台任务。
    - created_at：创建时间。
    - host：主机名，未来支持远程或多机诊断时会用到。
    """

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
    """一次 Agent 诊断运行的顶层记录。

    字段说明：
    - run_id：诊断运行 ID，steps/evidence/findings 都挂在这个 ID 下。
    - incident_id：关联的 ResourceIncident ID。
    - status：当前运行状态。
    - user_input：原始用户输入，复制到 run 里方便 trace 展示。
    - resource_type：本次诊断实际使用的资源类型。
    - agent_mode：Agent 模式，例如 deterministic。
    - final_report：返回给 CLI/API 的 Markdown 报告。
    - root_cause：简短的机器可读总结，目前通常是 finding_type 汇总。
    - summary：一行摘要，用于 `python main.py runs` 展示。
    - started_at：开始时间。
    - ended_at：结束时间。
    - error：run 级别错误；只有整个诊断失败时才填写。
    """

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
    """诊断 trace 里的一步。

    字段说明：
    - step_id：步骤 ID。
    - run_id：所属 DiagnosisRun ID。
    - step_index：步骤顺序，从 0 开始。
    - thought：Agent 为什么要做这一步。
    - action：动作名或工具名，例如 get_cpu_snapshot。
    - args：动作/工具参数。
    - observation：完整观察结果。工具步骤里通常保存 ToolExecutionResult 的 JSON。
    - observation_preview：给人看的短摘要，用于 CLI trace 展示。
    - latency_ms：动作耗时，单位毫秒。
    - status：步骤状态。
    - error：步骤级错误。
    - created_at：步骤创建时间。
    """

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
    """一次工具调用的持久化审计记录。

    字段说明：
    - call_id：工具调用 ID。
    - run_id：所属 DiagnosisRun ID。
    - step_id：触发这个工具调用的 DiagnosisStep ID。
    - tool_name：注册在 ToolRegistry 里的工具名。
    - args：校验后的工具参数。
    - result：完整工具返回结果。
    - preview：给人看的短摘要。
    - summary：更概括的摘要，通常由工具返回。
    - permission_level：工具权限等级。
    - latency_ms：工具耗时。
    - status：工具调用状态。
    - error：工具失败、超时或被审批拦截时的错误信息。
    - created_at：工具调用记录创建时间。
    """

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
    """detector 从工具结果里提取出来的一条具体证据。

    Evidence 比 Finding 更底层。例子：
    - “GPU 0 显存使用率 95%。”
    - “CPU load_avg_1m=12，cpu_count=8。”
    - “PID 123 占用 20GB RSS。”

    字段说明：
    - evidence_id：证据 ID，Finding 通过这个 ID 引用证据。
    - run_id：所属 DiagnosisRun ID。
    - source_tool：这条证据来自哪个工具。
    - category：证据类别，例如 gpu/cpu/memory/process/oom。
    - level：证据严重程度。
    - message：给人看的证据描述。
    - data：支撑这条证据的结构化原始数据子集。
    - confidence：detector 对这条证据的置信度，范围 0.0 到 1.0。
    - created_at：证据创建时间。
    """

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
    """挂在 DiagnosisFinding 上的建议操作。

    字段说明：
    - action：稳定的动作名，例如 inspect_top_cpu_processes、kill_process。
    - description：给人看的建议说明。
    - risk：动作风险等级。
    - requires_approval：这个动作是否需要人工审批。
    - command_preview：可选命令预览，只展示，不会自动执行。
    - reason：为什么根据当前证据推荐这个动作。

    注意：安全建议应该显式设置 requires_approval=False。
    危险建议，例如 kill_process，必须设置 True。
    """

    action: str
    description: str
    risk: RiskLevel = RiskLevel.SAFE
    requires_approval: bool = True
    command_preview: str | None = None
    reason: str


class DiagnosisFinding(StrictBaseModel):
    """detector 生成的一条诊断发现。

    Finding 会把一条或多条 EvidenceItem 归纳成一个诊断结论，例如：
    - gpu_memory_pressure
    - cpu_saturation
    - memory_pressure
    - oom_event

    字段说明：
    - finding_id：诊断发现 ID。
    - run_id：所属 DiagnosisRun ID。
    - finding_type：稳定的机器可读类型。
    - title：简短标题。
    - description：解释这个发现意味着什么。
    - evidence_ids：支撑这个发现的 EvidenceItem ID 列表。
    - confidence：这个发现的置信度，范围 0.0 到 1.0。
    - recommended_actions：针对这个发现的建议操作列表。
    - requires_approval：如果存在危险建议或需要审批的建议，则为 True。
    """

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
    """危险操作的人工审批记录。

    P3 只会在 finding/recommendation 上标记 requires_approval。
    P4 才会根据危险建议真正创建 Approval。

    字段说明：
    - approval_id：审批 ID。
    - run_id：是哪次 DiagnosisRun 产生了这个审批。
    - action：危险动作名，例如 kill_process。
    - args：动作参数，例如 {"pid": 123}。
    - reason：为什么请求这个动作。
    - risk：风险等级，通常是 dangerous。
    - status：审批状态。
    - created_at：审批创建时间。
    - decided_at：人工批准/拒绝的时间。
    - executed_at：模拟执行或真实执行的时间。
    """

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
