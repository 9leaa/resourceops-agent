from __future__ import annotations

from app.schemas import ResourceType,DiagnosisStep,DiagnosisFinding,EvidenceItem
from tools.registry import ToolExecutionResult

def build_p0_report(description: str, resource_type: ResourceType) -> str:
    return f"""## Resource Diagnosis Report

### 1. 问题概览
用户问题：{description}
诊断类型：{resource_type.value}

### 2. 当前阶段
V1-P0 已创建 ResourceOps 诊断 run，并完成 schema、CLI/API、Trace、Approval 和 ToolRegistry 基础骨架。

### 3. 关键证据
- P0 尚未执行真实 GPU / CPU / Memory 工具。
- V1-P1 将实现真实资源采集工具。

### 4. 可能根因
diagnosis not implemented in V1-P0

### 5. 建议操作
- 进入 V1-P1：实现 get_cpu_snapshot、get_memory_snapshot、get_gpu_snapshot 等真实工具。

### 6. 风险与审批
未自动执行危险操作。

### 7. 后续排查
V1-P2 后将根据 resource_type 执行固定诊断 plan。
""".strip()

def build_p2_report(
        description:str,
        resource_type:ResourceType,
        steps:list[DiagnosisStep],
        tool_results:list[ToolExecutionResult],
) -> str:
    """V1-P2 阶段报告函数，保留用于阶段历史；当前 ResourceAgent 使用 build_p3_report。"""

    tool_lines = []
    for step in steps:
        if step.action == "infer_resource_type":
            continue
        tool_lines.append(
            f"- {step.action}: {step.observation_preview or 'no preview'}"
        )

    if not tool_lines:
        tool_lines.append("- no resource tools executed")

    error_lines = [
        f"- {result.tool_name}: {result.error}"
        for result in tool_results
        if result.error
    ]
    if not error_lines:
        error_lines.append("- none")
    
    return f"""## Resource Diagnosis Report

### 1. 问题概览
用户问题：{description}
诊断类型：{resource_type.value}

### 2. 已执行资源检查
{chr(10).join(tool_lines)}

### 3. 工具错误
{chr(10).join(error_lines)}

### 4. 当前结论
V1-P2 已完成 deterministic plan 执行和 trace 记录。当前阶段只采集证据，不做最终根因判断。

### 5. 后续阶段
V1-P3 将实现 detectors，把工具结果转换为 gpu_memory_pressure、cpu_saturation、memory_pressure 等 findings。

### 6. 风险与审批
未自动执行危险操作。
""".strip()



def build_p3_report(
        description:str,
        resource_type:ResourceType,
        steps:list[DiagnosisStep],
        tool_results:list[ToolExecutionResult],
        evidence_items: list[EvidenceItem],
        findings: list[DiagnosisFinding],
) -> str:
    tool_lines = []
    for step in steps:
        if step.action == "infer_resource_type":
            continue
        tool_lines.append(
            f"- {step.action}: {step.observation_preview or 'no preview'}"
        )

    if not tool_lines:
        tool_lines.append("- no resource tools executed")

    evidence_lines = [
        f"- [{item.level}] {item.message}"
        for item in evidence_items
    ]

    if not evidence_lines:
        evidence_lines.append("- No detector evidence matched current thresholds.")

    finding_lines = []
    for finding in findings:
        finding_lines.append(
            f"- {finding.finding_type}: {finding.title} "
            f"(confidence={finding.confidence}, requires_approval={finding.requires_approval})"
        )

    if not finding_lines:
        finding_lines.append("- no findings")

    recommendation_lines = []
    for finding in findings:
        for action in finding.recommended_actions:
            recommendation_lines.append(
                f"- {action.action}: {action.description} "
            )
    if not recommendation_lines:
        recommendation_lines.append("- Continue monitoring or run a longer sampling diagnosis in a later stage.")

    error_lines = [
        f"- {result.tool_name}: {result.error}"
        for result in tool_results
        if result.error
    ]
    if not error_lines:
        error_lines.append("- none")
    
    return f"""## Resource Diagnosis Report

### 1. 问题概览
用户问题：{description}
诊断类型：{resource_type.value}

### 2. 资源检查
{chr(10).join(tool_lines)}

### 3. 关键证据
{chr(10).join(evidence_lines)}

### 4. 诊断发现
{chr(10).join(finding_lines)}

### 5. 建议操作
{chr(10).join(recommendation_lines)}

### 6. 工具错误
{chr(10).join(error_lines)}

### 7. 风险与审批
V1-P3 只生成 requires_approval 标记，不创建审批单；审批创建将在 V1-P4 实现。
""".strip()
