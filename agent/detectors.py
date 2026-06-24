"""把工具结果转换成关键证据和诊断发现的 detectors。

Detector 不直接调用工具，只分析 `ToolExecutionResult`。每个 detector 负责一种
资源异常规则，例如 GPU 显存压力、CPU saturation、内存压力或 OOM event。
"""

from __future__ import annotations

from typing import Any

from app.schemas import (
    DiagnosisFinding,
    EvidenceCategory,
    EvidenceItem,
    EvidenceLevel,
    Recommendation,
    RiskLevel,
)
from tools.registry import ToolExecutionResult


def run_detectors(
    run_id: str,
    tool_results: list[ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    results_by_tool = {result.tool_name: result for result in tool_results if isinstance(result.data, dict)}
    evidence_items: list[EvidenceItem] = []
    findings: list[DiagnosisFinding] = []

    detectors = [
        detect_gpu_unavailable,
        detect_gpu_memory_pressure,
        detect_gpu_low_utilization_cpu_bottleneck,
        detect_cpu_saturation,
        detect_single_process_cpu_hot,
        detect_memory_pressure,
        detect_swap_pressure,
        detect_oom_event,
        detect_memory_hogging_process,
    ]

    for detector in detectors:
        detector_evidence, detector_findings = detector(run_id, results_by_tool)
        evidence_items.extend(detector_evidence)
        findings.extend(detector_findings)

    return evidence_items, findings


def detect_gpu_unavailable(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    gpu = data_for(results, "get_gpu_snapshot")
    if not gpu:
        return [], []

    if gpu.get("available") is not False:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="get_gpu_snapshot",
        category=EvidenceCategory.GPU,
        level=EvidenceLevel.WARNING,
        message=f"GPU snapshot is unavailable: {gpu.get('error') or 'unknown reason'}.",
        data=gpu,
        confidence=0.9,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="gpu_unavailable",
        title="GPU information is unavailable",
        description="nvidia-smi is unavailable or failed, so GPU utilization and memory cannot be diagnosed from local GPU telemetry.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.9,
        recommended_actions=[
            Recommendation(
                action="check_nvidia_smi",
                description="Check whether NVIDIA driver and nvidia-smi are installed and accessible.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview="nvidia-smi",
                reason="GPU tool returned unavailable status.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_gpu_memory_pressure(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    gpu = data_for(results, "get_gpu_snapshot")
    if not gpu or gpu.get("available") is False:
        return [], []

    gpus = gpu.get("gpus") or []
    pressured = [
        item for item in gpus
        if number(item.get("memory_used_percent")) >= 90
    ]
    if not pressured:
        return [], []

    gpu_proc = data_for(results, "list_gpu_processes") or {}
    processes = gpu_proc.get("processes") or []

    evidence_items: list[EvidenceItem] = []
    evidence_ids: list[str] = []

    for item in pressured:
        evidence = EvidenceItem(
            run_id=run_id,
            source_tool="get_gpu_snapshot",
            category=EvidenceCategory.GPU,
            level=EvidenceLevel.CRITICAL,
            message=(
                f"GPU {item.get('index')} memory usage is "
                f"{item.get('memory_used_percent')}% "
                f"({item.get('memory_used_mb')} / {item.get('memory_total_mb')} MB)."
            ),
            data=item,
            confidence=0.95,
        )
        evidence_items.append(evidence)
        evidence_ids.append(evidence.evidence_id)

    if processes:
        top_process = max(processes, key=lambda item: number(item.get("used_memory_mb")))
        evidence = EvidenceItem(
            run_id=run_id,
            source_tool="list_gpu_processes",
            category=EvidenceCategory.PROCESS,
            level=EvidenceLevel.WARNING,
            message=(
                f"GPU process PID {top_process.get('pid')} uses "
                f"{top_process.get('used_memory_mb')} MB GPU memory."
            ),
            data=top_process,
            confidence=0.85,
        )
        evidence_items.append(evidence)
        evidence_ids.append(evidence.evidence_id)

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="gpu_memory_pressure",
        title="GPU memory pressure detected",
        description="At least one GPU is using 90% or more of its memory. This can block new training or inference jobs from allocating CUDA memory.",
        evidence_ids=evidence_ids,
        confidence=0.92,
        recommended_actions=[
            Recommendation(
                action="inspect_gpu_processes",
                description="Review GPU process owners and commands before taking action.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview="nvidia-smi",
                reason="GPU memory pressure should be attributed to concrete processes first.",
            ),
            Recommendation(
                action="kill_process",
                description="Terminate an unrelated GPU process only after manual confirmation.",
                risk=RiskLevel.DANGEROUS,
                requires_approval=True,
                command_preview="kill <pid>",
                reason="Killing a process is destructive and must be approved.",
            ),
        ],
        requires_approval=True,
    )
    return evidence_items, [finding]


def detect_gpu_low_utilization_cpu_bottleneck(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    gpu = data_for(results, "get_gpu_snapshot")
    cpu = data_for(results, "get_cpu_snapshot")
    if not gpu or not cpu or gpu.get("available") is False:
        return [], []

    gpus = gpu.get("gpus") or []
    if not gpus:
        return [], []

    avg_gpu_util = sum(number(item.get("utilization_gpu_percent")) for item in gpus) / len(gpus)
    load_avg_1m = number(cpu.get("load_avg_1m"))
    cpu_count = number(cpu.get("cpu_count"))

    if avg_gpu_util >= 20 or cpu_count <= 0 or load_avg_1m <= cpu_count:
        return [], []

    ev_gpu = EvidenceItem(
        run_id=run_id,
        source_tool="get_gpu_snapshot",
        category=EvidenceCategory.GPU,
        level=EvidenceLevel.WARNING,
        message=f"Average GPU utilization is low at {avg_gpu_util:.2f}%.",
        data={"avg_gpu_utilization_percent": avg_gpu_util, "gpus": gpus},
        confidence=0.85,
    )
    ev_cpu = EvidenceItem(
        run_id=run_id,
        source_tool="get_cpu_snapshot",
        category=EvidenceCategory.CPU,
        level=EvidenceLevel.WARNING,
        message=f"CPU load_avg_1m {load_avg_1m} is higher than cpu_count {cpu_count}.",
        data=cpu,
        confidence=0.85,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="cpu_bottleneck_for_gpu",
        title="CPU bottleneck may be limiting GPU utilization",
        description="GPU utilization is low while CPU load is above available CPU cores, which is consistent with dataloader or CPU preprocessing bottlenecks.",
        evidence_ids=[ev_gpu.evidence_id, ev_cpu.evidence_id],
        confidence=0.85,
        recommended_actions=[
            Recommendation(
                action="tune_dataloader_workers",
                description="Review dataloader worker count and CPU preprocessing cost.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview=None,
                reason="Low GPU utilization with high CPU load often points to input pipeline bottlenecks.",
            )
        ],
        requires_approval=False,
    )
    return [ev_gpu, ev_cpu], [finding]


def detect_cpu_saturation(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    cpu = data_for(results, "get_cpu_snapshot")
    if not cpu:
        return [], []

    load_avg_1m = number(cpu.get("load_avg_1m"))
    cpu_count = number(cpu.get("cpu_count"))
    overall = number(cpu.get("overall_cpu_percent"))

    saturated = (cpu_count > 0 and load_avg_1m > cpu_count * 1.2) or overall > 85
    if not saturated:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="get_cpu_snapshot",
        category=EvidenceCategory.CPU,
        level=EvidenceLevel.WARNING,
        message=f"CPU appears saturated: load_avg_1m={load_avg_1m}, cpu_count={cpu_count}, overall_cpu_percent={overall}.",
        data=cpu,
        confidence=0.9,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="cpu_saturation",
        title="CPU saturation detected",
        description="CPU load or utilization is high enough to affect local workloads.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.9,
        recommended_actions=[
            Recommendation(
                action="inspect_top_cpu_processes",
                description="Review top CPU processes and determine whether the load is expected.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview="ps aux --sort=-%cpu | head",
                reason="CPU saturation should be attributed to processes before remediation.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_single_process_cpu_hot(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    data = data_for(results, "list_top_cpu_processes")
    if not data:
        return [], []

    processes = data.get("processes") or []
    if not processes:
        return [], []

    top = max(processes, key=lambda item: number(item.get("cpu_percent")))
    if number(top.get("cpu_percent")) <= 150:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="list_top_cpu_processes",
        category=EvidenceCategory.PROCESS,
        level=EvidenceLevel.WARNING,
        message=f"Process PID {top.get('pid')} is using {top.get('cpu_percent')}% CPU.",
        data=top,
        confidence=0.85,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="cpu_single_process_hot",
        title="Single process has high CPU usage",
        description="One process is consuming more than 150% CPU, which may indicate a hot training worker, preprocessing job, or runaway process.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.85,
        recommended_actions=[
            Recommendation(
                action="inspect_process",
                description="Inspect the hot process command, owner, threads, and memory before taking action.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview=f"python main.py inspect-process {top.get('pid')}",
                reason="The process should be inspected before any destructive action.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_memory_pressure(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    mem = data_for(results, "get_memory_snapshot")
    if not mem:
        return [], []

    used_percent = number(mem.get("used_percent"))
    available_mb = number(mem.get("available_mb"))

    if used_percent <= 85 and available_mb >= 1024:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="get_memory_snapshot",
        category=EvidenceCategory.MEMORY,
        level=EvidenceLevel.WARNING if used_percent < 95 else EvidenceLevel.CRITICAL,
        message=f"Memory pressure detected: used_percent={used_percent}, available_mb={available_mb}.",
        data=mem,
        confidence=0.9,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="memory_pressure",
        title="Memory pressure detected",
        description="System memory usage is high or available memory is low.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.9,
        recommended_actions=[
            Recommendation(
                action="inspect_top_memory_processes",
                description="Review top RSS processes and identify expected versus unexpected memory consumers.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview="ps aux --sort=-rss | head",
                reason="Memory pressure should be attributed to processes before remediation.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_swap_pressure(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    mem = data_for(results, "get_memory_snapshot")
    if not mem:
        return [], []

    swap_total = number(mem.get("swap_total_mb"))
    swap_used_percent = number(mem.get("swap_used_percent"))

    if swap_total <= 0 or swap_used_percent <= 30:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="get_memory_snapshot",
        category=EvidenceCategory.MEMORY,
        level=EvidenceLevel.WARNING,
        message=f"Swap usage is elevated: swap_used_percent={swap_used_percent}.",
        data=mem,
        confidence=0.85,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="swap_pressure",
        title="Swap pressure detected",
        description="Swap usage is high enough to slow down training, data loading, or interactive workloads.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.85,
        recommended_actions=[
            Recommendation(
                action="reduce_memory_pressure",
                description="Reduce memory-heavy workloads or lower batch size before swap grows further.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview=None,
                reason="High swap usage often causes severe performance degradation.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_oom_event(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    oom = data_for(results, "check_oom_events")
    if not oom:
        return [], []

    events = oom.get("events") or []
    if not events:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="check_oom_events",
        category=EvidenceCategory.OOM,
        level=EvidenceLevel.CRITICAL,
        message=f"Found {len(events)} recent OOM-related kernel events.",
        data={"events": events[:5], "source": oom.get("source")},
        confidence=0.9,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="oom_event",
        title="Recent OOM event detected",
        description="Kernel logs contain OOM-related events, indicating one or more processes may have been killed due to memory exhaustion.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.9,
        recommended_actions=[
            Recommendation(
                action="review_oom_process",
                description="Review the OOM event lines and identify the killed process or memory-heavy workload.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview="dmesg --ctime | grep -i oom",
                reason="OOM events provide concrete process evidence for memory failures.",
            )
        ],
        requires_approval=False,
    )
    return [evidence], [finding]


def detect_memory_hogging_process(
    run_id: str,
    results: dict[str, ToolExecutionResult],
) -> tuple[list[EvidenceItem], list[DiagnosisFinding]]:
    mem = data_for(results, "get_memory_snapshot")
    top_mem = data_for(results, "list_top_memory_processes")
    if not mem or not top_mem:
        return [], []

    total_mb = number(mem.get("total_mb"))
    processes = top_mem.get("processes") or []
    if total_mb <= 0 or not processes:
        return [], []

    top = max(processes, key=lambda item: number(item.get("rss_mb")))
    if number(top.get("rss_mb")) <= total_mb * 0.4:
        return [], []

    evidence = EvidenceItem(
        run_id=run_id,
        source_tool="list_top_memory_processes",
        category=EvidenceCategory.PROCESS,
        level=EvidenceLevel.WARNING,
        message=f"Process PID {top.get('pid')} uses {top.get('rss_mb')} MB RSS, more than 40% of system memory.",
        data={"process": top, "total_mb": total_mb},
        confidence=0.85,
    )

    finding = DiagnosisFinding(
        run_id=run_id,
        finding_type="memory_process_hogging",
        title="Single process is using a large share of memory",
        description="One process uses more than 40% of system memory and may be the main contributor to memory pressure.",
        evidence_ids=[evidence.evidence_id],
        confidence=0.85,
        recommended_actions=[
            Recommendation(
                action="inspect_process",
                description="Inspect the memory-heavy process before taking action.",
                risk=RiskLevel.SAFE,
                requires_approval=False,
                command_preview=f"python main.py inspect-process {top.get('pid')}",
                reason="Large RSS should be attributed and confirmed before remediation.",
            ),
            Recommendation(
                action="kill_process",
                description="Terminate the memory-heavy process only after manual confirmation.",
                risk=RiskLevel.DANGEROUS,
                requires_approval=True,
                command_preview=f"kill {top.get('pid')}",
                reason="Killing a process is destructive and must be approved.",
            ),
        ],
        requires_approval=True,
    )
    return [evidence], [finding]


def data_for(results: dict[str, ToolExecutionResult], tool_name: str) -> dict[str, Any] | None:
    result = results.get(tool_name)
    if result is None or not isinstance(result.data, dict):
        return None
    return result.data


def number(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
