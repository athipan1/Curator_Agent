from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SkillCreateRequest
from app.registry import SkillRegistry


VALID_CODE = "def signal(price, regime):\n    return {'signal': 'buy' if price > 100 else 'hold', 'confidence': 0.75, 'reason': regime}"
INVALID_OUTPUT_CODE = "def signal(price, regime):\n    return {'signal': 'strong_buy', 'confidence': 1.2, 'reason': regime}"


class _NoDatabase:
    enabled = False

    def get_skill_backtest_status(self, skill_id):
        return {"status": "skipped", "data": {"passed": False}}

    def rank_skills(self, **kwargs):
        return {"status": "skipped", "data": []}

    def create_skill_execution_log(self, payload, correlation_id=None):
        return {"status": "skipped", "data": {}}


def _client(tmp_path, monkeypatch, *, code=VALID_CODE, input_schema=None, output_schema=None):
    monkeypatch.setenv("CURATOR_SEED_BACKTEST_SKILL", "false")
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    skill = registry.register(
        SkillCreateRequest(
            skill_id="schema-skill",
            name="Schema Skill",
            description="Skill with strict input and output contracts.",
            code=code,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
        )
    )
    registry.approve(skill.skill_id)
    app = create_app(registry=registry, database_client=_NoDatabase())
    return TestClient(app), skill


def _input_schema():
    return {
        "type": "object",
        "required": ["price", "regime"],
        "additionalProperties": False,
        "properties": {
            "price": {"type": "number", "exclusiveMinimum": 0},
            "regime": {"type": "string", "enum": ["momentum", "value"]},
        },
    }


def _output_schema():
    return {
        "type": "object",
        "required": ["signal", "confidence", "reason"],
        "additionalProperties": False,
        "properties": {
            "signal": {"type": "string", "enum": ["buy", "hold", "sell"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "minLength": 1},
        },
    }


def test_valid_input_and_output_pass_contracts(tmp_path, monkeypatch):
    client, skill = _client(
        tmp_path,
        monkeypatch,
        input_schema=_input_schema(),
        output_schema=_output_schema(),
    )

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.5, "regime": "momentum"}},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] == "success"
    assert data["output"]["signal"] == "buy"
    assert data["schema_contract"] == {
        "input_valid": True,
        "output_valid": True,
        "errors": [],
        "input_schema_enforced": True,
        "output_schema_enforced": True,
    }


def test_missing_required_input_is_rejected_before_execution(tmp_path, monkeypatch):
    client, skill = _client(
        tmp_path,
        monkeypatch,
        input_schema=_input_schema(),
        output_schema=_output_schema(),
    )

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.5}},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] == "input_schema_rejected"
    assert data["error"] == "skill_input_schema_validation_failed"
    assert "$input.regime: required property is missing" in data["schema_contract"]["errors"]
    assert data["output"] == {}


def test_wrong_type_enum_and_extra_input_are_reported(tmp_path, monkeypatch):
    client, skill = _client(
        tmp_path,
        monkeypatch,
        input_schema=_input_schema(),
        output_schema=_output_schema(),
    )

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={
            "inputs": {
                "price": "120",
                "regime": "unknown",
                "secret": "not-allowed",
            }
        },
    )

    data = response.json()["data"]
    errors = data["schema_contract"]["errors"]
    assert data["execution_status"] == "input_schema_rejected"
    assert "$input.price: expected number, got str" in errors
    assert "$input.regime: value is not in allowed enum" in errors
    assert "$input.secret: additional property is not allowed" in errors


def test_invalid_output_is_removed_and_rejected(tmp_path, monkeypatch):
    client, skill = _client(
        tmp_path,
        monkeypatch,
        code=INVALID_OUTPUT_CODE,
        input_schema=_input_schema(),
        output_schema=_output_schema(),
    )

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.5, "regime": "momentum"}},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] in {"failed", "output_schema_rejected"}
    if data["execution_status"] == "output_schema_rejected":
        assert data["error"] == "skill_output_schema_validation_failed"
        assert data["output"] == {}
        assert data["rejected_output"]["signal"] == "strong_buy"
        assert data["schema_contract"]["output_valid"] is False
    else:
        assert data["error"] == "confidence_must_be_between_0_and_1"


def test_empty_schemas_remain_backward_compatible(tmp_path, monkeypatch):
    client, skill = _client(tmp_path, monkeypatch)

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.5, "regime": "momentum"}},
    )

    data = response.json()["data"]
    assert data["execution_status"] == "success"
    assert data["schema_contract"]["input_schema_enforced"] is False
    assert data["schema_contract"]["output_schema_enforced"] is False
