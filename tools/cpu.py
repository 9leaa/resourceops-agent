"""CPU resource diagnostic tools."""
from __future__ import annotations

from datetime import datetime, timezone
import time
import psutil
from pydantic import Field


from app.schemas import StrictBaseModel

class GetCpuSnapshotInput(StrictBaseModel):
    pass

class ListTopCpuProcessesInput(StrictBaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    min_cpu_percent:float = Field(default=0.0, ge=0.0)

def get_cpu_snapshot(args: GetCpuSnapshotInput) -> dict:
    load1, load5, load15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count(logical=True) or 0

    per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)
    overall_cpu = round(sum(per_cpu) / len(per_cpu), 2) if per_cpu else 0.0
    load_per_cpu_1m = round(load1 / cpu_count, 2) if cpu_count else None

    return {
        "cpu_count": cpu_count,
        "load_avg_1m": round(load1, 2),
        "load_avg_5m": round(load5, 2),
        "load_avg_15m": round(load15, 2),
        "load_per_cpu_1m": load_per_cpu_1m,
        "overall_cpu_percent": overall_cpu,
        "per_cpu_percent": per_cpu,
        "preview": f"cpu={overall_cpu}%, load1={load1:.2f}, cores={cpu_count}",
        "summary": "current CPU load and utilization snapshot",
    }

def list_top_cpu_processes(args: ListTopCpuProcessesInput) -> dict:
    procs = []

    for proc in psutil.process_iter(
        ["pid", "username", "memory_percent", "create_time", "cmdline"]
    ):
        try:
            proc.cpu_percent(interval=None)
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    time.sleep(0.1)

    rows = []
    for proc in procs:
        try:
            cpu_percent = proc.cpu_percent(interval=None)
            if cpu_percent < args.min_cpu_percent:
                continue
            info = proc.info
            mem = proc.memory_info()

            rows.append(
                {
                    "pid": info["pid"],
                    "username": info.get("username"),
                    "cpu_percent": round(cpu_percent, 2),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 4),
                    "rss_mb": round(mem.rss / 1024 / 1024, 2),
                    "command": _cmdline_to_string(info.get("cmdline")),
                    "started_at": _format_ts(info.get("create_time")),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    rows.sort(key=lambda item: item["cpu_percent"], reverse=True)
    rows = rows[: args.limit]

    return {
        "processes": rows,
        "preview": f"top_cpu_processes={len(rows)}",
        "summary": "top processes sorted by current CPU percent",
    }
def _cmdline_to_string(cmdline: list[str] | None) -> str:
    if not cmdline:
        return ""
    return " ".join(cmdline)


def _format_ts(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()