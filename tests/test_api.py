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


def test_register_and_fetch_skill(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))

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
    payload = response.json()["data"]
    assert payload["validation_status"] == "validated"
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
    assert "forbidden_ast_node: Import" in payload["validation_errors"]
    assert "forbidden_attribute_call: os.system" in payload["validation_errors"]


def test_list_and_search_skills(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    client = TestClient(create_app(registry))
    client.post(
        "/skills/register",
        json={
            "name": "RSI Momentum Filter",
            "description": "Momentum filter for high volume conditions.",
            "code": VALID_SKILL,
            "tags": ["technical", "momentum"],
        },
    )

    listed = client.get("/skills?tag=technical&validation_status=validated")
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    searched = client.get("/skills/search?q=momentum")
    assert searched.status_code == 200
    assert searched.json()["data"][0]["name"] == "RSI Momentum Filter"
