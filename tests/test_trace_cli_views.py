import json

from app.cli import main


def trace_fixture() -> dict:
    llm_call = {
        "call_version": "v1",
        "call_id": "llmcall_test",
        "purpose": "planner",
        "model": "fake-model",
        "temperature": 0.1,
        "status": "success",
        "latency_ms": 12,
        "prompt_length": 100,
        "response_length": 20,
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "system_prompt_preview": "system",
        "user_prompt_preview": "user",
        "response_preview": "response",
        "full_payload_stored": False,
        "error_type": None,
        "error": None,
        "created_at": "2026-07-11T00:00:00Z",
    }
    return {
        "run": {
            "run_id": "run_test",
            "status": "completed",
            "resource_type": "cpu",
            "user_input": "why",
            "summary": "done",
            "started_at": "2026-07-11T00:00:00Z",
            "ended_at": "2026-07-11T00:00:01Z",
            "root_cause": None,
        },
        "steps": [
            {
                "step_index": 0,
                "action": "llm_planner",
                "observation_preview": "accepted",
                "observation": {
                    "source": "llm",
                    "used_llm_plan": True,
                    "fallback_reason": None,
                    "llm_call": llm_call,
                },
            }
        ],
        "tool_calls": [],
        "findings": [],
        "evidence_items": [],
        "approvals": [],
        "todos": [],
        "action_results": [],
    }


class FakeTraceStore:
    def get_trace(self, run_id: str) -> dict:
        assert run_id == "run_test"
        return trace_fixture()


def test_trace_default_and_summary_json_views(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.TraceStore", FakeTraceStore)

    assert main(["trace", "run_test"]) == 0
    assert "# Run Summary: run_test" in capsys.readouterr().out

    assert main(["trace", "run_test", "--summary-json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary_version"] == "v1"


def test_trace_step_llm_and_legacy_json_views(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.TraceStore", FakeTraceStore)

    assert main(["trace", "run_test", "--step", "llm_planner"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["action"] == "llm_planner"

    assert main(["trace", "run_test", "--llm"]) == 0
    assert json.loads(capsys.readouterr().out)["calls"][0]["call_id"] == "llmcall_test"

    assert main(["trace", "run_test", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["run_id"] == "run_test"
