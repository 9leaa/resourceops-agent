from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ResourceType


def test_p2_agent_executes_cpu_plan() -> None:
    result = ResourceAgent().diagnose(ResourceIncident(description="为什么 CPU 很高？"))

    actions = [step.action for step in result.steps]

    assert result.run.status == "completed"
    assert result.run.resource_type == ResourceType.CPU
    assert "infer_resource_type" in actions
    assert "get_cpu_snapshot" in actions
    assert "list_top_cpu_processes" in actions
    assert result.tool_results
    assert "V1-P2" in result.final_report


def test_p2_agent_executes_mixed_plan_for_slow_training() -> None:
    result = ResourceAgent().diagnose(ResourceIncident(description="训练任务很慢"))

    actions = [step.action for step in result.steps]

    assert result.run.resource_type == ResourceType.MIXED
    assert "get_gpu_snapshot" in actions
    assert "get_cpu_snapshot" in actions
    assert "get_memory_snapshot" in actions
    assert "check_oom_events" in actions