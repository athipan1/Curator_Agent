from fastapi.testclient import TestClient

from app.main import create_app
from app.registry import SkillRegistry


VALID_SKILL = """
def bollinger_rsi_signal(close_values, rsi_value):
    if rsi_value < 30:
        return {"signal": "buy", "confidence": 0.7}
    if rsi_value > 70:
        return {"signal": "sell", "confidence": 0.7}
    return {"signal": "hold", "confidence": 0.5}
"""


def _register_valid_skill(client: TestClient) -> dict:
    response = client.post(
        "/skills/register",
        json={
            "name": "Bollinger RSI Signal",
            "description": "Combines RSI thresholds with a future Bollinger filter.",
            "code": VALID_SKILL,
            "tags": ["technical", "rsi"],
            "market_context": {"asset_class": "stocks", "regime": "mean_reversion"},
            "input_schema": {"close_values": "list[float]", "rsi_value": "float"},
            "output_schema": {"signal": "str", "confidence": "float"},
            "source_agent": "Technical_Agent",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_register_and_fetch_skill(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

    payload = _register_valid_skill(client)
    assert payload["validation_status"] == "validated"
    assert payload["approval_status"] == "draft"
    assert payload["code_hash"]

    fetched = client.get(f"/skills/{payload['skill_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["code"] == VALID_SKILL


def test_register_rejected_skill_still_records_validation_status(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

    response = client.post(
        "/skills/register",
        json={
            "name": "Unsafe Skill",
            "description": "Should be rejected by static validation.",
            "code": "import os\ndef bad():\n    return os.system('echo unsafe')",
            "tags": ["unsafe"],
        },
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["validation_status"] == "rejected"
    assert payload["approval_status"] == "draft"
    assert "forbidden_ast_node: Import" in payload["validation_errors"]
    assert "forbidden_attribute_call: os.system" in payload["validation_errors"]


def test_approve_and_deprecate_validated_skill(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))
    payload = _register_valid_skill(client)

    approved = client.post(
        f"/skills/{payload['skill_id']}/approve",
        json={"approved_by": "risk-owner", "reason": "paper backtest reviewed"},
    )
    assert approved.status_code == 200
    approved_payload = approved.json()["data"]
    assert approved_payload["approval_status"] == "approved"
    assert approved_payload["lifecycle_notes"][-1]["action"] == "approved"

    listed = client.get("/skills?approval_status=approved")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["skill_id"] == payload["skill_id"]

    deprecated = client.post(
        f"/skills/{payload['skill_id']}/deprecate",
        json={"approved_by": "risk-owner", "reason": "replaced by safer version"},
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["data"]["approval_status"] == "deprecated"


def test_rejected_skill_cannot_be_approved(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

    response = client.post(
        "/skills/register",
        json={
            "name": "Unsafe Skill",
            "description": "Should stay draft and rejected.",
            "code": "import os\ndef bad():\n    return os.system('echo unsafe')",
        },
    )
    skill_id = response.json()["data"]["skill_id"]

    approved = client.post(f"/skills/{skill_id}/approve", json={})

    assert approved.status_code == 400
    assert approved.json()["detail"] == "only_validated_skills_can_be_approved"


def test_list_and_search_skills(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))
    payload = client.post(
        "/skills/register",
        json={
            "name": "RSI Momentum Filter",
            "description": "Momentum filter for high volume conditions.",
            "code": VALID_SKILL,
            "tags": ["technical", "momentum"],
        },
    ).json()["data"]
    client.post(f"/skills/{payload['skill_id']}/approve", json={})

    listed = client.get("/skills?tag=technical&validation_status=validated&approval_status=approved")
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    searched = client.get("/skills/search?q=momentum&approval_status=approved")
    assert searched.status_code == 200
    assert searched.json()["data"][0]["name"] == "RSI Momentum Filter"
