from trace.summary import build_run_summary, render_run_summary_markdown


def sample_trace() -> dict:
    return {
        "run": {
            "run_id": "run_test",
            "status": "completed",
            "resource_type": "memory",
            "user_input": "为什么内存快满了？",
            "root_cause": "memory_process_hogging",
            "started_at": "2026-07-11T00:00:00Z",
            "ended_at": "2026-07-11T00:00:02Z",
        },
        "steps": [
            {
                "action": "llm_planner",
                "latency_ms": 120,
                "observation": {
                    "source": "llm",
                    "used_llm_plan": True,
                    "fallback_reason": None,
                    "latency_ms": 120,
                },
            },
            {
                "action": "build_tool_plan",
                "observation": {
                    "tool_plan": {
                        "planner_mode": "llm",
                        "steps": [
                            {"tool_name": "get_memory_snapshot"},
                            {"tool_name": "list_top_memory_processes"},
                        ],
                    }
                },
            },
            {
                "action": "llm_report",
                "latency_ms": 240,
                "observation": {"fallback_reason": None, "latency_ms": 240},
            },
        ],
        "tool_calls": [
            {"status": "success", "latency_ms": 10},
            {"status": "error", "latency_ms": 20},
        ],
        "evidence_items": [{"evidence_id": "ev_1"}],
        "findings": [
            {
                "finding_type": "memory_process_hogging",
                "title": "Single process uses most memory",
                "confidence": 0.9,
            }
        ],
        "approvals": [{"status": "executed"}],
        "action_results": [
            {
                "mode": "real",
                "status": "success",
                "execution": {"changed_system_state": True},
            }
        ],
    }


def test_summary_contains_run_planning_diagnosis_and_action_state() -> None:
    summary = build_run_summary(sample_trace())

    assert summary["run"]["status"] == "completed"
    assert summary["run"]["duration_ms"] == 2000
    assert summary["planning"]["selected_tools"] == [
        "get_memory_snapshot",
        "list_top_memory_processes",
    ]
    assert summary["diagnosis"]["top_findings"][0]["finding_type"] == "memory_process_hogging"
    assert summary["approval"]["executed"] == 1
    assert summary["actions"]["changed_system_state"] is True
    assert summary["llm"]["planner_latency_ms"] == 120
    assert "report_generated_before_action_execution" in summary["warnings"]


def test_summary_markdown_is_human_readable() -> None:
    rendered = render_run_summary_markdown(build_run_summary(sample_trace()))

    assert "# Run Summary: run_test" in rendered
    assert "get_memory_snapshot" in rendered
    assert "memory_process_hogging" in rendered
