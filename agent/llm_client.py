from __future__ import annotations

import os
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

    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        """调用 OpenAI-compatible chat completion，返回纯文本内容。"""

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("invalid llm response shape") from exc

        return str(content).strip()

    def generate_report(self, prompt: str) -> str:
        return self.generate_text(
            system_prompt=(
                "你是 ResourceOps 诊断报告撰写器。"
                "你只能根据用户提供的结构化证据写报告，不能编造事实，"
                "不能新增工具结果，不能改变审批状态。"
            ),
            user_prompt=prompt,
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
    )


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
