from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SkillCreateRequest
from app.registry import SkillRegistry


SKILL_CODE = """
def score_signal(final_score):
    return {"result": "ok", "confidence": final_score, "reason": "fixture"}
"""


def test_curator_seeds_backtest_skill_with_deterministic_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_SEED_BACKTEST_SKILL", "true")
    monkeypatch.setenv("CURATOR_SEED_BACKTEST_SKILL_ID", "hourly-sma-crossover")
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))

    client = TestClient(create_app(registry=registry))

    response = client.get("/skills/hourly-sma-crossover")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_id"] == "hourly-sma-crossover"
    assert data["validation_status"] == "validated"
    assert data["approval_status"] == "approved"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["data"]["seeded_backtest_skill_id"] == "hourly-sma-crossover"


def test_register_is_idempotent_for_existing_skill_id(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    first = registry.register(
        SkillCreateRequest(
            skill_id="fixed-skill",
            name="Fixed Skill",
            description="First version",
            code=SKILL_CODE,
        )
    )
    second = registry.register(
        SkillCreateRequest(
            skill_id="fixed-skill",
            name="Fixed Skill Updated",
            description="Second version should not duplicate",
            code=SKILL_CODE,
        )
    )

    assert first.skill_id == "fixed-skill"
    assert second.skill_id == "fixed-skill"
    assert len(registry.list()) == 1
