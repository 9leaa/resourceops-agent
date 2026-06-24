from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident
from trace.store import TraceStore


def test_trace_saves_p2_tool_calls(tmp_path) -> None:
    trace_store = TraceStore(tmp_path / "resourceops.sqlite3")
    result = ResourceAgent().diagnose(ResourceIncident(description="为什么 CPU 很高？"))

    trace_store.save_agent_result(result)
    trace = trace_store.get_trace(result.run.run_id)

    assert trace["run"]["status"] == "completed"
    assert len(trace["steps"]) >= 3
    assert len(trace["tool_calls"]) == len(result.tool_results)

    tool_names = {call["tool_name"] for call in trace["tool_calls"]}
    assert "get_cpu_snapshot" in tool_names
    assert "list_top_cpu_processes" in tool_names