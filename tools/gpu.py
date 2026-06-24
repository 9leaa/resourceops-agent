"""GPU tools arrive in V1-P1."""

from __future__ import annotations

import shutil
import subprocess

import psutil
from pydantic import Field

from app.schemas import StrictBaseModel


class GetGpuSnapshotInput(StrictBaseModel):
    pass


class ListGpuProcessesInput(StrictBaseModel):
    limit: int = Field(default=50, ge=1, le=200)


def get_gpu_snapshot(args: GetGpuSnapshotInput) -> dict:
    if shutil.which("nvidia-smi") is None:
        return {
            "available": False,
            "error": "nvidia-smi not found",
            "gpus": [],
            "preview": "gpu unavailable: nvidia-smi not found",
            "summary": "GPU snapshot unavailable",
        }

    command = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]

    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        return {
            "available": False,
            "error": "nvidia-smi timed out",
            "gpus": [],
            "preview": "gpu unavailable: nvidia-smi timeout",
            "summary": "GPU snapshot unavailable",
        }

    if completed.returncode != 0:
        return {
            "available": False,
            "error": completed.stderr.strip() or completed.stdout.strip(),
            "gpus": [],
            "preview": "gpu unavailable: nvidia-smi failed",
            "summary": "GPU snapshot unavailable",
        }

    gpus = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 8:
            continue

        index, name, driver, util, mem_used, mem_total, temp, power = parts
        used = _to_float(mem_used)
        total = _to_float(mem_total)

        gpus.append(
            {
                "index": _to_int(index),
                "name": name,
                "driver_version": driver,
                "utilization_gpu_percent": _to_float(util),
                "memory_used_mb": used,
                "memory_total_mb": total,
                "memory_used_percent": round((used / total) * 100, 2) if total else 0.0,
                "temperature_c": _to_float(temp),
                "power_draw_w": _to_float(power),
            }
        )

    return {
        "available": True,
        "driver_version": gpus[0]["driver_version"] if gpus else None,
        "cuda_version": None,
        "gpus": gpus,
        "preview": f"gpus={len(gpus)}",
        "summary": "current GPU utilization and memory snapshot",
    }


def list_gpu_processes(args: ListGpuProcessesInput) -> dict:
    if shutil.which("nvidia-smi") is None:
        return {
            "available": False,
            "error": "nvidia-smi not found",
            "processes": [],
            "preview": "gpu processes unavailable: nvidia-smi not found",
            "summary": "GPU process lookup unavailable",
        }

    command = [
        "nvidia-smi",
        "--query-compute-apps=pid,gpu_uuid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]

    completed = subprocess.run(command, text=True, capture_output=True, timeout=5, check=False)
    if completed.returncode != 0:
        return {
            "available": False,
            "error": completed.stderr.strip() or completed.stdout.strip(),
            "processes": [],
            "preview": "gpu processes unavailable: nvidia-smi failed",
            "summary": "GPU process lookup unavailable",
        }

    rows = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue

        pid_text, gpu_uuid, process_name, used_memory = parts
        pid = _to_int(pid_text)
        proc_info = _process_info(pid)

        rows.append(
            {
                "pid": pid,
                "gpu_uuid": gpu_uuid,
                "process_name": process_name,
                "used_memory_mb": _to_float(used_memory),
                "username": proc_info.get("username"),
                "command": proc_info.get("command"),
            }
        )

    rows = rows[: args.limit]

    return {
        "available": True,
        "processes": rows,
        "preview": f"gpu_processes={len(rows)}",
        "summary": "processes currently using GPU memory",
    }


def _process_info(pid: int) -> dict:
    try:
        proc = psutil.Process(pid)
        return {
            "username": proc.username(),
            "command": " ".join(proc.cmdline()),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {"username": None, "command": ""}


def _to_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def _to_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0