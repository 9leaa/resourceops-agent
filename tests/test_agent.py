from agent.resource_agent import ResourceAgent
from app.schemas import ResourceIncident, ResourceType


def test_p0_agent_creates_completed_run() -> None:
    result = ResourceAgent().diagnose(ResourceIncident(description="为什么 CPU 很高？"))
    assert result.run.status == "completed"
    assert result.run.resource_type == ResourceType.CPU
    assert result.steps
    assert "V1-P0" in result.final_report
    assert not result.requires_approval
