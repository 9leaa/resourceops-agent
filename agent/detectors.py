"""Detector placeholders for V1-P3.

V1-P0 keeps detector interfaces out of the main agent so ResourceAgent can
grow into GPU/CPU/Memory-specific detectors without becoming a large method.
"""

from __future__ import annotations

from app.schemas import DiagnosisFinding
from tools.registry import ToolExecutionResult


def run_detectors(run_id: str, tool_results: list[ToolExecutionResult]) -> list[DiagnosisFinding]:
    return []

