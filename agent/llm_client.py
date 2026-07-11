from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx


class LlmClient(Protocol):
    def generate_report(self, prompt: str) -> str:
        ...


@dataclass(frozen=True)
class OpenAICompatibleLlmClient:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: float = 20.0
    temperature: float = 0.2
    service_tier: str | None = None
    planner_max_tokens: int = 512
    report_max_tokens: int = 640
    max_retries: int = 1
    retry_backoff_seconds: float = 1.0

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        """Generate a bounded LLM planner response."""

        return self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=self.planner_max_tokens,
        )

    def _generate_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """Call an OpenAI-compatible chat completion and return plain text."""

        payload: dict[str, object] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.service_tier:
            payload["service_tier"] = self.service_tier

        response = self._post_with_retry(payload)

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("invalid llm response shape") from exc

        return str(content).strip()

    def _post_with_retry(self, payload: dict[str, object]) -> httpx.Response:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code not in {429, 502, 503, 504} or attempt >= self.max_retries:
                    response.raise_for_status()
                    return response
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.max_retries:
                    raise
            time.sleep(self.retry_backoff_seconds * (2**attempt))

        raise RuntimeError("unreachable LLM retry state")

    def generate_report(self, prompt: str) -> str:
        return self._generate_text(
            system_prompt=(
                "你是 ResourceOps 的诊断报告撰写器。"
                "输入数据已经由确定性工具和 Detector 生成。"
                "你只能重组和解释输入中的事实，不能新增事实、工具结果、命令或操作。"
                "必须准确区分：发现、建议、待审批、dry-run、真实执行。"
            ),
            user_prompt=prompt,
            max_tokens=self.report_max_tokens,
        )


def build_default_llm_client_from_env() -> LlmClient | None:
    load_env_file()

    api_key = get_env("RESOURCEOPS_LLM_API_KEY", "CCSWITCH_API_KEY", "OPENAI_API_KEY")
    base_url = get_env("RESOURCEOPS_LLM_BASE_URL", "CCSWITCH_BASE_URL", "OPENAI_BASE_URL")
    model = get_env("RESOURCEOPS_LLM_MODEL", "CCSWITCH_MODEL", "OPENAI_MODEL")

    if not api_key or not base_url or not model:
        return None

    return OpenAICompatibleLlmClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=float(os.getenv("RESOURCEOPS_LLM_TIMEOUT_SECONDS", "20")),
        temperature=float(os.getenv("RESOURCEOPS_LLM_TEMPERATURE", "0.2")),
        service_tier=get_env("RESOURCEOPS_LLM_SERVICE_TIER"),
        planner_max_tokens=get_positive_int_env("RESOURCEOPS_LLM_PLANNER_MAX_TOKENS", default=512),
        report_max_tokens=get_positive_int_env("RESOURCEOPS_LLM_REPORT_MAX_TOKENS", default=640),
        max_retries=get_non_negative_int_env("RESOURCEOPS_LLM_MAX_RETRIES", default=1),
        retry_backoff_seconds=float(os.getenv("RESOURCEOPS_LLM_RETRY_BACKOFF_SECONDS", "1.0")),
    )


def get_positive_int_env(name: str, *, default: int) -> int:
    raw_value = os.getenv(name)
    if not raw_value or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def get_non_negative_int_env(name: str, *, default: int) -> int:
    raw_value = os.getenv(name)
    if not raw_value or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def get_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return None


def load_env_file(path: Path | str | None = None) -> None:
    env_path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value
