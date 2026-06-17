from __future__ import annotations

from app.schemas import ResourceType


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

