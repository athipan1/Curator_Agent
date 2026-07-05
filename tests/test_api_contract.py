from fastapi.testclient import TestClient

from app.main import create_app


REQUIRED_CONTRACT_FIELDS = {
    "status",
    "agent_type",
    "version",
    "schema_version",
    "timestamp",
    "correlation_id",
    "data",
    "metadata",
    "error",
    "confidence_score",
}


def assert_contract_response(payload):
    assert REQUIRED_CONTRACT_FIELDS.issubset(payload.keys())
    assert payload["agent_type"] == "curator-agent"
    assert payload["version"] == "0.1.0"
    assert payload["schema_version"] == "1.0"


def test_version_endpoint_uses_contract_response():
    app = create_app()
    client = TestClient(app)
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert_contract_response(payload)
    assert payload["data"]["api_contract"] == "multi-agent-trading-api-contract"
    assert payload["data"]["schema_version"] == "1.0"
    assert payload["data"]["service_version"] == "0.2.0"


def test_ready_endpoint_uses_contract_response():
    app = create_app()
    client = TestClient(app)
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert_contract_response(payload)
    assert payload["data"]["ready"] is True
    assert payload["metadata"]["contract_source"] == "curator-agent-runtime-contract"


def test_existing_health_endpoint_still_works():
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["agent_type"] == "curator-agent"
    assert payload["version"] == "0.1.0"
    assert payload["data"]["status"] == "healthy"
