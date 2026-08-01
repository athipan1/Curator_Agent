import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.main_legacy import create_app as create_legacy_app
from app.registry import SkillRegistry


VALID_SKILL = """
def signal(value):
    return {"signal": "hold", "confidence": 0.5, "reason": str(value)}
"""


def _configure_auth(monkeypatch):
    monkeypatch.setenv("CURATOR_REQUIRE_API_KEY", "true")
    monkeypatch.setenv("CURATOR_READ_API_KEY", "read-key")
    monkeypatch.setenv("CURATOR_EXECUTE_API_KEY", "execute-key")
    monkeypatch.setenv("CURATOR_ADMIN_API_KEY", "admin-key")


def _client(tmp_path, monkeypatch):
    _configure_auth(monkeypatch)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    return registry, TestClient(create_app(registry))


def test_operational_endpoints_remain_open(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    for path in ("/health", "/ready", "/version"):
        response = client.get(path)
        assert response.status_code == 200


def test_missing_key_is_rejected_with_contract_envelope(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    response = client.get("/skills")

    assert response.status_code == 401
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "missing_api_key"
    assert payload["metadata"]["required_role"] == "read"


def test_legacy_entrypoint_enforces_same_auth_policy(tmp_path, monkeypatch):
    _configure_auth(monkeypatch)
    registry = SkillRegistry(str(tmp_path / "legacy-skills.sqlite3"))
    client = TestClient(create_legacy_app(registry))

    missing = client.get("/skills")
    authorized = client.get("/skills", headers={"X-API-KEY": "read-key"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"
    assert authorized.status_code == 200


def test_read_key_cannot_mutate_skill_lifecycle(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/skills/register",
        headers={"X-API-KEY": "read-key"},
        json={
            "name": "Read key cannot register",
            "description": "Authentication role test.",
            "code": VALID_SKILL,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_api_key_role"
    assert response.json()["metadata"]["required_role"] == "admin"


def test_admin_registers_and_execute_key_runs_approved_skill(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    admin_headers = {"X-API-KEY": "admin-key"}

    registered = client.post(
        "/skills/register",
        headers=admin_headers,
        json={
            "name": "Authenticated Skill",
            "description": "Role-authenticated execution test.",
            "code": VALID_SKILL,
        },
    )
    assert registered.status_code == 200
    skill_id = registered.json()["data"]["skill_id"]

    approved = client.post(
        f"/skills/{skill_id}/approve",
        headers=admin_headers,
        json={"approved_by": "security-test"},
    )
    assert approved.status_code == 200

    read_only_execution = client.post(
        f"/skills/{skill_id}/execute",
        headers={"X-API-KEY": "read-key"},
        json={"inputs": {"value": "read-key"}},
    )
    assert read_only_execution.status_code == 403

    executed = client.post(
        f"/skills/{skill_id}/execute",
        headers={"X-API-KEY": "execute-key"},
        json={"inputs": {"value": "execute-key"}},
    )
    assert executed.status_code == 200
    assert executed.json()["data"]["execution_status"] == "success"


def test_higher_privilege_keys_can_use_lower_privilege_routes(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)

    execute_read = client.get("/skills", headers={"X-API-KEY": "execute-key"})
    admin_read = client.get("/skills", headers={"X-API-KEY": "admin-key"})

    assert execute_read.status_code == 200
    assert admin_read.status_code == 200


def test_shared_key_alias_supports_all_roles(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_API_KEY", "shared-key")
    monkeypatch.setenv("CURATOR_REQUIRE_API_KEY", "true")
    monkeypatch.delenv("CURATOR_READ_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_EXECUTE_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_ADMIN_API_KEY", raising=False)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

    response = client.post(
        "/skills/register",
        headers={"X-API-KEY": "shared-key"},
        json={
            "name": "Shared Key Skill",
            "description": "Compatibility key test.",
            "code": VALID_SKILL,
        },
    )

    assert response.status_code == 200


def test_production_fails_startup_without_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CURATOR_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_READ_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_EXECUTE_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_ADMIN_API_KEY", raising=False)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))

    with pytest.raises(RuntimeError, match="API keys are missing"):
        create_app(registry)


def test_development_without_keys_remains_backward_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CURATOR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_READ_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_EXECUTE_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_ADMIN_API_KEY", raising=False)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

    response = client.get("/skills")

    assert response.status_code == 200
