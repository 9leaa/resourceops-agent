"""Memory tools arrive in V1-P1."""

from __future__ import annotations

import subprocess

import psutil
from pydantic import Field

from app.schemas import StrictBaseModel


class GetMemorySnapshotInput(StrictBaseModel):
    pass


class ListTopMemoryProcessesInput(StrictBaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class CheckOomEventsInput(StrictBaseModel):
    limit: int = Field(default=20, ge=1, le=200)


def get_memory_snapshot(args: GetMemorySnapshotInput) -> dict:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "total_mb": round(mem.total / 1024 / 1024, 2),
        "available_mb": round(mem.available / 1024 / 1024, 2),
        "used_mb": round(mem.used / 1024 / 1024, 2),
        "used_percent": mem.percent,
        "swap_total_mb": round(swap.total / 1024 / 1024, 2),
        "swap_used_mb": round(swap.used / 1024 / 1024, 2),
        "swap_used_percent": swap.percent,
        "preview": f"memory={mem.percent}%, swap={swap.percent}%",
        "summary": "current memory and swap snapshot",
    }


def list_top_memory_processes(args: ListTopMemoryProcessesInput) -> dict:
    rows = []

    for proc in psutil.process_iter(["pid", "username", "memory_percent", "cmdline"]):
        try:
            info = proc.info
            mem = proc.memory_info()
            rows.append(
                {
                    "pid": info["pid"],
                    "username": info.get("username"),
                    "rss_mb": round(mem.rss / 1024 / 1024, 2),
                    "vms_mb": round(mem.vms / 1024 / 1024, 2),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 4),
                    "command": _cmdline_to_string(info.get("cmdline")),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    rows.sort(key=lambda item: item["rss_mb"], reverse=True)
    rows = rows[: args.limit]

    return {
        "processes": rows,
        "preview": f"top_memory_processes={len(rows)}",
        "summary": "top processes sorted by RSS memory",
    }


def check_oom_events(args: CheckOomEventsInput) -> dict:
    commands = [
        ["dmesg", "--ctime"],
        ["journalctl", "-k", "--no-pager", "-n", "300"],
    ]

    errors = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            )
        except FileNotFoundError:
            errors.append(f"{command[0]} not found")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"{command[0]} timed out")
            continue

        output = completed.stdout + "\n" + completed.stderr
        lower = output.lower()

        if completed.returncode != 0 and ("permission" in lower or "operation not permitted" in lower):
            errors.append(f"{command[0]} permission denied")
            continue

        events = [
            line.strip()
            for line in output.splitlines()
            if "oom" in line.lower() or "out of memory" in line.lower() or "killed process" in line.lower()
        ][-args.limit :]

        return {
            "available": True,
            "source": command[0],
            "events": events,
            "preview": f"oom_events={len(events)} source={command[0]}",
            "summary": "recent kernel OOM-related events",
        }

    return {
        "available": False,
        "reason": "; ".join(errors) or "no oom source available",
        "events": [],
        "preview": "oom_events unavailable",
        "summary": "OOM event lookup unavailable",
    }


def _cmdline_to_string(cmdline: list[str] | None) -> str:
    if not cmdline:
        return ""
    return " ".join(cmdline)