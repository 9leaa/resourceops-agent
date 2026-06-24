from __future__ import annotations

from app.schemas import ResourceType,DiagnosisStep
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