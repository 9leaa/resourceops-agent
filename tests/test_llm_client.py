import pytest

from agent.llm_client import OpenAICompatibleLlmClient, build_default_llm_client_from_env


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "OK"}}]}


class FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield from self.lines


def test_llm_client_uses_fast_tier_and_planner_token_limit(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(*_args, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("agent.llm_client.httpx.post", fake_post)
    client = OpenAICompatibleLlmClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        service_tier="fast",
        planner_max_tokens=512,
        report_max_tokens=768,
    )

    assert client.generate_text(system_prompt="system", user_prompt="planner") == "OK"

    assert captured["json"] == {
        "model": "test-model",
        "temperature": 0.2,
        "max_tokens": 512,
        "service_tier": "fast",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "planner"},
        ],
    }


def test_llm_client_uses_report_token_limit_without_service_tier(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(*_args, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("agent.llm_client.httpx.post", fake_post)
    client = OpenAICompatibleLlmClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        service_tier=None,
        planner_max_tokens=512,
        report_max_tokens=768,
    )

    assert client.generate_report("report") == "OK"

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["max_tokens"] == 768
    assert "service_tier" not in payload


def test_llm_client_reads_latency_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("RESOURCEOPS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("RESOURCEOPS_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RESOURCEOPS_LLM_MODEL", "test-model")
    monkeypatch.setenv("RESOURCEOPS_LLM_SERVICE_TIER", "fast")
    monkeypatch.setenv("RESOURCEOPS_LLM_PLANNER_MAX_TOKENS", "321")
    monkeypatch.setenv("RESOURCEOPS_LLM_REPORT_MAX_TOKENS", "654")
    monkeypatch.setenv("RESOURCEOPS_LLM_MAX_RETRIES", "2")

    client = build_default_llm_client_from_env()

    assert isinstance(client, OpenAICompatibleLlmClient)
    assert client.service_tier == "fast"
    assert client.planner_max_tokens == 321
    assert client.report_max_tokens == 654
    assert client.max_retries == 2


def test_llm_client_rejects_invalid_output_limit(monkeypatch) -> None:
    monkeypatch.setenv("RESOURCEOPS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("RESOURCEOPS_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RESOURCEOPS_LLM_MODEL", "test-model")
    monkeypatch.setenv("RESOURCEOPS_LLM_PLANNER_MAX_TOKENS", "0")

    with pytest.raises(ValueError, match="RESOURCEOPS_LLM_PLANNER_MAX_TOKENS"):
        build_default_llm_client_from_env()


def test_llm_client_retries_transient_gateway_error(monkeypatch) -> None:
    responses = [FakeResponse(502), FakeResponse(200)]
    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    monkeypatch.setattr("agent.llm_client.httpx.post", fake_post)
    monkeypatch.setattr("agent.llm_client.time.sleep", lambda _seconds: None)
    client = OpenAICompatibleLlmClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        max_retries=1,
    )

    assert client.generate_report("report") == "OK"
    assert calls == 2


def test_llm_client_streams_report_chunks(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_stream(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return FakeStreamResponse(
            [
                'data: {"choices":[{"delta":{"content":"Hel"}}]}',
                'data: {"choices":[{"delta":{"content":"lo"}}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr("agent.llm_client.httpx.stream", fake_stream)
    client = OpenAICompatibleLlmClient(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        report_max_tokens=768,
    )

    assert "".join(client.stream_report("report")) == "Hello"

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["stream"] is True
    assert payload["max_tokens"] == 768
