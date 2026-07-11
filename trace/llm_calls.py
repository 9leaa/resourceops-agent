from __future__ import annotations

import hashlib
import os
from typing import Any

from agent.report_context import redact_sensitive_text
from app.schemas import new_id, utc_now


def build_llm_call_record(
    *,
    purpose: str,
    client: Any,
    system_prompt: str,
    user_prompt: str,
    response: str,
    status: str,
    latency_ms: int,
    error_type: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    store_payload = os.getenv("RESOURCEOPS_STORE_LLM_PAYLOADS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    record: dict[str, Any] = {
        "call_version": "v1",
        "call_id": new_id("llmcall"),
        "purpose": purpose,
        "model": getattr(client, "model", None),
        "temperature": getattr(client, "temperature", None),
        "status": status,
        "latency_ms": max(0, latency_ms),
        "prompt_length": len(user_prompt),
        "response_length": len(response),
        "prompt_sha256": _sha256(system_prompt + "\n\n" + user_prompt),
        "response_sha256": _sha256(response) if response else None,
        "system_prompt_preview": _preview(redact_sensitive_text(system_prompt)),
        "user_prompt_preview": _preview(redact_sensitive_text(user_prompt)),
        "response_preview": _preview(redact_sensitive_text(response)) if response else None,
        "full_payload_stored": store_payload,
        "error_type": error_type,
        "error": redact_sensitive_text(error) if error else None,
        "created_at": utc_now().isoformat().replace("+00:00", "Z"),
    }
    if store_payload:
        record["full_payload"] = {
            "system_prompt": redact_sensitive_text(system_prompt),
            "user_prompt": redact_sensitive_text(user_prompt),
            "response": redact_sensitive_text(response),
        }
    return record


def public_llm_call(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "full_payload"}


def extract_llm_calls(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for step in steps:
        observation = step.get("observation") or {}
        record = observation.get("llm_call")
        if isinstance(record, dict):
            records.append(record)
        elif step.get("action") in {"llm_planner", "llm_report"}:
            purpose = "planner" if step.get("action") == "llm_planner" else "report"
            records.append(
                {
                    "call_version": "legacy",
                    "call_id": f"legacy-{step.get('step_id') or purpose}",
                    "purpose": purpose,
                    "model": None,
                    "temperature": None,
                    "status": observation.get("status") or step.get("status"),
                    "latency_ms": int(observation.get("latency_ms") or step.get("latency_ms") or 0),
                    "prompt_length": int(observation.get("prompt_length") or 0),
                    "response_length": int(observation.get("response_length") or 0),
                    "prompt_sha256": None,
                    "response_sha256": None,
                    "system_prompt_preview": None,
                    "user_prompt_preview": None,
                    "response_preview": observation.get("response_preview"),
                    "full_payload_stored": False,
                    "error_type": observation.get("error_type"),
                    "error": observation.get("error") or step.get("error"),
                    "created_at": step.get("created_at"),
                }
            )
    return records


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _preview(value: str, limit: int = 500) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
