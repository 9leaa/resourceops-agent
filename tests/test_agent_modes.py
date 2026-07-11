import json
import sqlite3

from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ToolCallStatus, ToolPermissionLevel
from tools.registry import ToolExecutionResult
from trace.store import TraceStore


class RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict | None = None) -> ToolExecutionResult:
        self.calls.append((name, args or {}))
        return ToolExecutionResult(
            tool_name=name,
            permission_level=ToolPermissionLevel.SAFE,
            status=ToolCallStatus.SUCCESS,
            data={},
            preview=f"{name} fixture",
            summary=f"{name} fixture",
            latency_ms=0,
            validated_args=args or {},
        )


class FakeCombinedClient:
    def generate_text(self, *, system_prompt: str, user_prompt: str) -> str:
        assert "工具目录 JSON" in user_prompt
        return json.dumps(
            {
                "steps": [
                    {
                        "tool_name": "get_cpu_snapshot",
                        "args": {},
                        "reason": "先看 CPU 快照。",
                        "expected_result": "CPU load。",
                    }
                ]
            },
            ensure_ascii=False,
        )

    def generate_report(self, prompt: str) -> str:
        assert "诊断数据" in prompt
        return """## 问题概览
LLM 最终报告。

## 关键证据
使用已有证据。

## 诊断发现
基于 detector 结果。

## 建议操作
继续观察。

## 审批状态
无待审批危险操作。

## 风险说明
危险操作不会自动执行。
"""


def test_planner_and_report_modes_can_both_use_llm() -> None:
    registry = RecordingRegistry()

    result = ResourceAgent(
        registry=registry,
        planner_mode="llm",
        report_mode="llm",
        llm_client=FakeCombinedClient(),
    ).diagnose(ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu"))

    assert result.run.agent_mode == "llm_full"
    assert result.run.planner_mode == "llm"
    assert result.run.report_mode == "llm"
    assert result.tool_plan.planner_mode == "llm"
    assert [name for name, _args in registry.calls] == ["get_cpu_snapshot"]
    assert "LLM 最终报告" in result.final_report

    actions = [step.action for step in result.steps]
    assert "llm_planner" in actions
    assert "build_report_context" in actions
    assert "llm_report" in actions


def test_legacy_llm_planner_does_not_enable_llm_report() -> None:
    result = ResourceAgent(
        registry=RecordingRegistry(),
        agent_mode="llm_planner",
        llm_client=FakeCombinedClient(),
    ).diagnose(ResourceIncident(description="为什么 CPU 很高？", resource_type="cpu"))

    assert result.run.agent_mode == "llm_planner"
    assert result.run.planner_mode == "llm"
    assert result.run.report_mode == "template"
    assert "## Resource Diagnosis Report" in result.final_report
    assert all(step.action != "llm_report" for step in result.steps)


def test_trace_store_migrates_old_diagnosis_runs_table(tmp_path) -> None:
    db_path = tmp_path / "old_resourceops.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE diagnosis_runs (
                run_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                status TEXT NOT NULL,
                user_input TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                agent_mode TEXT NOT NULL,
                final_report TEXT,
                root_cause TEXT,
                summary TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                error TEXT
            )
            """
        )

    trace_store = TraceStore(db_path)

    with trace_store.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(diagnosis_runs)").fetchall()
        }

    assert "planner_mode" in columns
    assert "report_mode" in columns
