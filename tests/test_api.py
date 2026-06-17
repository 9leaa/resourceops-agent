from fastapi.testclient import TestClient

from app.api import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_diagnose_endpoint() -> None:
    client = TestClient(app)
    response = client.post("/diagnose", json={"description": "为什么内存快满了？", "resource_type": "memory"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["resource_type"] == "memory"
    assert payload["run"]["status"] == "completed"
