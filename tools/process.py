"""Process inspection diagnostic tools."""

from __future__ import annotations

from datetime import datetime, timezone

import psutil
from pydantic import Field

from app.schemas import StrictBaseModel


class InspectProcessInput(StrictBaseModel):
    pid: int = Field(..., gt=0)


def inspect_process(args: InspectProcessInput) -> dict:
    try:
        proc = psutil.Process(args.pid)
    except psutil.NoSuchProcess:
        return {
            "available": False,
            "pid": args.pid,
            "error": "process not found",
            "preview": f"pid={args.pid} not found",
            "summary": "process lookup failed",
        }

    try:
        memory = proc.memory_info()
        children = []
        for child in proc.children(recursive=False):
            children.append({"pid": child.pid, "name": _safe_call(child.name)})

        return {
            "available": True,
            "pid": proc.pid,
            "ppid": proc.ppid(),
            "username": _safe_call(proc.username),
            "status": _safe_call(proc.status),
            "cmdline": " ".join(_safe_call(proc.cmdline) or []),
            "cwd": _safe_call(proc.cwd),
            "create_time": _format_ts(_safe_call(proc.create_time)),
            "cpu_percent": proc.cpu_percent(interval=0.0),
            "memory_info": {
                "rss_mb": round(memory.rss / 1024 / 1024, 2),
                "vms_mb": round(memory.vms / 1024 / 1024, 2),
            },
            "open_files_count": len(_safe_call(proc.open_files) or []),
            "num_threads": _safe_call(proc.num_threads),
            "children": children,
            "preview": f"pid={proc.pid} status={_safe_call(proc.status)}",
            "summary": "process inspection result",
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
        return {
            "available": False,
            "pid": args.pid,
            "error": f"{exc.__class__.__name__}: {exc}",
            "preview": f"pid={args.pid} unavailable",
            "summary": "process inspection unavailable",
        }


def _safe_call(func):
    try:
        return func()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _format_ts(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
