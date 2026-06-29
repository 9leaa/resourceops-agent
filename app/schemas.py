"""ResourceOps Agent 的核心数据结构。

这个文件是项目的数据合同层。CLI、API、Agent、Trace、Approval 和测试都依赖
这里的对象来传递结构化数据。读代码时可以把它理解成系统里所有“名词”的定义：

- ResourceIncident：用户输入的一次资源问题。
- DiagnosisRun：Agent 对这个问题做的一次诊断运行。
- ToolCatalog：当前系统可用工具的结构化目录。
- ToolPlan：本次诊断准备执行的结构化工具计划。
- PlannedToolCall：ToolPlan 中的一步工具调用计划。
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

class TodoStatus(str, Enum):
    """诊断任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"

class TodoLevel(str, Enum):
    """任务层级。

    PHASE：大任务阶段，例如 Planning tools / Tool execution。
    TASK：阶段内部小任务，例如 get_cpu_snapshot。
    """

    PHASE = "phase"
    TASK = "task"


class TodoDisplayGroup(str, Enum):
    """任务展示分组。"""

    PLANNING = "planning"
    TOOLS = "tools"
    REPORT = "report"
    APPROVAL = "approval"
    ACTIONS = "actions"


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


class PlannerMode(str, Enum):
    """工具计划由谁生成。

    DETERMINISTIC：由当前固定规则 planner 生成。
    LLM：由 LLM planner 生成，并已通过 PlanValidator 校验。
    FALLBACK：LLM planner 失败或计划不合法时使用的兜底计划。
    """

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    FALLBACK = "fallback"

class AgentPlannerMode(str, Enum):
    """run 级别的工具规划模式。

    DETERMINISTIC：固定规则 planner 决定工具。
    LLM：LLM planner 先提出工具计划，再经过 PlanValidator 校验。
    """

    DETERMINISTIC = "deterministic"
    LLM = "llm"


class ReportMode(str, Enum):
    """最终报告生成模式。

    TEMPLATE：使用本地固定模板报告。
    LLM：使用 LLM 在受控上下文内改写报告。
    """

    TEMPLATE = "template"
    LLM = "llm"

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
    planner_mode: AgentPlannerMode = AgentPlannerMode.DETERMINISTIC
    report_mode: ReportMode = ReportMode.TEMPLATE
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


class ToolCatalogItem(StrictBaseModel):
    """给 planner / LLM 使用的单个工具说明。

    ToolRegistry 负责注册和执行工具，ToolCatalogItem 负责把工具能力表达清楚。
    后续 LLM planner 只能根据这类结构化目录选择工具，不能凭空编工具名。

    字段说明：
    - name：工具名，必须能在 ToolRegistry 中找到。
    - description：工具用途说明。
    - input_schema：工具参数的 JSON Schema，来自工具输入模型。
    - permission_level：工具权限等级。
    - requires_approval：是否需要人工审批；dangerous 工具必须为 True。
    - timeout_seconds：工具超时时间。
    - retry：工具失败后的重试次数。
    - tags：工具标签，例如 cpu/process/snapshot。
    - resource_types：这个工具适合哪些资源类型的诊断。
    """

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    permission_level: ToolPermissionLevel = ToolPermissionLevel.SAFE
    requires_approval: bool = False
    timeout_seconds: float = Field(default=5.0, gt=0.0)
    retry: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    resource_types: list[ResourceType] = Field(default_factory=list)


class ToolCatalog(StrictBaseModel):
    """当前系统可用工具的结构化目录。

    P8 先把 ToolRegistry 暴露成稳定目录，供 deterministic planner 使用。
    P9/P10 后，LLM planner 也应该只看这个目录来生成 ToolPlan。

    字段说明：
    - catalog_version：目录格式版本。
    - tools：所有可用工具说明。
    - total_tools：工具数量，便于 trace 和测试快速检查。
    - generated_at：目录生成时间。
    """

    catalog_version: str = "p8"
    tools: list[ToolCatalogItem] = Field(default_factory=list)
    total_tools: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=utc_now)


class PlannedToolCall(StrictBaseModel):
    """ToolPlan 中的一步工具调用计划。

    它不是工具执行结果，只是“准备调用哪个工具、带什么参数、为什么调用”。
    真正执行后才会生成 ToolExecutionResult 和 ToolCall。

    字段说明：
    - planned_call_id：计划步骤 ID。
    - step_index：计划内顺序，从 0 开始。
    - tool_name：计划调用的工具名。
    - args：计划传给工具的参数。
    - reason：为什么需要这一步工具。
    - expected_result：期望这一步拿到什么信息。
    - permission_level：工具权限等级，来自 ToolCatalog。
    - requires_approval：这一步是否需要人工审批。
    - required：这一步是否是必要步骤；未来可用于跳过可选步骤。
    - tags：工具标签快照，方便 trace 展示和后续筛选。
    """

    planned_call_id: str = Field(default_factory=lambda: new_id("pcall"))
    step_index: int = Field(..., ge=0)
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str
    expected_result: str | None = None
    permission_level: ToolPermissionLevel = ToolPermissionLevel.SAFE
    requires_approval: bool = False
    required: bool = True
    tags: list[str] = Field(default_factory=list)


class ToolPlan(StrictBaseModel):
    """一次诊断运行的工具执行计划。

    P8 的核心对象。它把“要执行哪些工具”从零散 list[PlannedAction] 升级为
    可校验、可追踪、可复用的结构化计划。deterministic planner 和 P9 的
    LLM planner 都必须输出同样的结构。

    字段说明：
    - plan_id：计划 ID。
    - planner_mode：计划生成方式，例如 deterministic。
    - resource_type：本计划面向的资源类型。
    - user_question：用户原始问题，保存为计划上下文。
    - steps：按顺序排列的工具调用计划。
    - max_steps：本次计划最多允许执行多少步。
    - budget：预算信息，例如 max_tool_calls，未来可加入 max_latency_ms。
    - fallback_plan：备用工具计划，P8 先保留为空。
    - tool_catalog_version：生成计划时使用的 ToolCatalog 版本。
    - created_at：计划生成时间。
    """

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    planner_mode: PlannerMode = PlannerMode.DETERMINISTIC
    resource_type: ResourceType
    user_question: str
    steps: list[PlannedToolCall] = Field(default_factory=list)
    max_steps: int = Field(default=0, ge=0)
    budget: dict[str, Any] = Field(default_factory=dict)
    fallback_plan: list[PlannedToolCall] = Field(default_factory=list)
    tool_catalog_version: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

class DiagnosisTodo(StrictBaseModel):
    """一次诊断 run 中的可追踪任务。"""

    todo_id: str = Field(default_factory=lambda: new_id("todo"))
    run_id: str
    todo_index: int = Field(..., ge=0)
    title: str
    status: TodoStatus = TodoStatus.PENDING
    level: TodoLevel = TodoLevel.TASK
    parent_todo_id: str | None = None
    display_group: TodoDisplayGroup = TodoDisplayGroup.TOOLS
    sort_order: int = Field(default=0, ge=0)
    source: str = "tool_plan"
    tool_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    planned_call_id: str | None = None
    approval_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    result_preview: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

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


class ResourceAgentResult(StrictBaseModel):
    """ResourceAgent.diagnose() 返回给 CLI/API/Trace 的完整结果。

    字段说明：
    - run：本次诊断运行的顶层状态。
    - tool_plan：本次诊断使用的结构化工具计划。
    - steps：诊断步骤，包括资源类型推断和每个工具调用。
    - tool_results：工具层返回的标准化结果。
    - evidence_items：detector 生成的关键证据。
    - findings：detector 生成的诊断发现。
    - final_report：最终展示给用户的 Markdown 报告。
    - requires_approval：本次诊断是否产生待审批危险建议。
    - approvals：审批记录快照，保存为 dict 是为了和持久化/HTTP JSON 输出保持一致。
    """

    run: DiagnosisRun
    tool_plan: ToolPlan | None = None
    steps: list[DiagnosisStep] = Field(default_factory=list)
    tool_results: list[Any] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    findings: list[DiagnosisFinding] = Field(default_factory=list)
    final_report: str
    requires_approval: bool = False
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    todos: list[DiagnosisTodo] = Field(default_factory=list)
