from agent.resource_agent import ResourceAgent
from app.schemas import ResourceAgentResult, ResourceIncident, ResourceType


def test_p4_agent_executes_cpu_plan_and_runs_detectors() -> None:
    result = ResourceAgent().diagnose(ResourceIncident(description="为什么 CPU 很高？"))

    actions = [step.action for step in result.steps]

    assert isinstance(result, ResourceAgentResult)
    assert result.run.status == "completed"
    assert result.run.resource_type == ResourceType.CPU
    assert "infer_resource_type" in actions
    assert "get_cpu_snapshot" in actions
    assert "list_top_cpu_processes" in actions
    assert result.tool_results
    assert result.evidence_items is not None
    assert result.findings is not None
    assert "审批" in result.final_report
    assert "危险操作不会自动执行" in result.final_report


def test_p4_agent_executes_mixed_plan_for_slow_training() -> None:
    result = ResourceAgent().diagnose(ResourceIncident(description="训练任务很慢"))

    actions = [step.action for step in result.steps]

    assert result.run.resource_type == ResourceType.MIXED
    assert "get_gpu_snapshot" in actions
    assert "get_cpu_snapshot" in actions
    assert "get_memory_snapshot" in actions
    assert "check_oom_events" in actions
    assert "诊断发现" in result.final_report
