from app.schemas import DiagnosisRun, ResourceIncident, ResourceType


def test_resource_incident_strips_description() -> None:
    incident = ResourceIncident(description="  为什么 CPU 很高？  ")
    assert incident.description == "为什么 CPU 很高？"


def test_diagnosis_run_defaults_to_mixed() -> None:
    incident = ResourceIncident(description="训练很慢")
    run = DiagnosisRun(incident_id=incident.incident_id, user_input=incident.description)
    assert run.resource_type == ResourceType.MIXED
