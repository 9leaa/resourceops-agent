from __future__ import annotations

from app.schemas import ResourceType


def infer_resource_type(description: str, explicit: ResourceType | str | None = None) -> ResourceType:
    if explicit:
        return ResourceType(explicit)

    text = description.lower()
    gpu_keywords = ("gpu", "cuda", "显存", "nvidia", "nvidia-smi")
    cpu_keywords = ("cpu", "load", "卡顿", "打满", "core")
    memory_keywords = ("memory", "内存", "swap", "oom", "out of memory")
    mixed_keywords = ("slow", "训练慢", "bottleneck", "瓶颈", "很慢", "慢")

    has_gpu = any(keyword in text for keyword in gpu_keywords)
    has_cpu = any(keyword in text for keyword in cpu_keywords)
    has_memory = any(keyword in text for keyword in memory_keywords)
    if sum([has_gpu, has_cpu, has_memory]) > 1:
        return ResourceType.MIXED
    if has_gpu:
        return ResourceType.GPU
    if has_cpu:
        return ResourceType.CPU
    if has_memory:
        return ResourceType.MEMORY
    if any(keyword in text for keyword in mixed_keywords):
        return ResourceType.MIXED
    return ResourceType.MIXED
