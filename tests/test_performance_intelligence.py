from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SkillCreateRequest
from app.performance_intelligence import assess_performance_decay, calibrate_confidence
from app.registry import SkillRegistry


CODE = "def signal(price):\n    return {'signal': 'buy', 'confidence': 0.9, 'reason': 'momentum'}"


class _DatabaseWithHistory:
    enabled = True

    def rank_skills(self, **kwargs):
        return {
            "status": "success",
            "data": [
                {
                    "skill_id": "calibrated-skill",
                    "sample_size": 90,
                    "win_rate": 0.60,
                    "skill_score": 0.58,
                    "profit_factor": 1.10,
                }
            ],
        }

    def get_skill_backtest_status(self, skill_id):
        return {"status": "success", "data": {"passed": True}}

    def create_skill_execution_log(self, payload, correlation_id=None):
        return {"status": "success", "data": {"execution_log_id": "log-1"}}


class _NoHistoryDatabase(_DatabaseWithHistory):
    enabled = False

    def rank_skills(self, **kwargs):
        return {"status": "skipped", "data": []}


def _client(tmp_path, monkeypatch, database):
    monkeypatch.setenv("CURATOR_SEED_BACKTEST_SKILL", "false")
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    skill = registry.register(
        SkillCreateRequest(
            skill_id="calibrated-skill",
            name="Calibrated Skill",
            description="Skill with confidence calibration.",
            code=CODE,
            output_schema={
                "type": "object",
                "required": ["signal", "confidence", "reason"],
                "properties": {
                    "signal": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        )
    )
    registry.approve(skill.skill_id)
    return TestClient(create_app(registry=registry, database_client=database)), skill


def test_calibration_blends_raw_confidence_with_empirical_reliability():
    result = calibrate_confidence(
        0.9,
        {"sample_size": 90, "win_rate": 0.60},
        prior_strength=30,
    )

    assert result["calibration_status"] == "calibrated"
    assert result["evidence_weight"] == 0.75
    assert result["calibrated_confidence"] == 0.675


def test_calibration_keeps_raw_value_when_history_is_missing():
    result = calibrate_confidence(0.8, {})

    assert result["calibration_status"] == "insufficient_performance_history"
    assert result["calibrated_confidence"] == 0.8


def test_decay_requires_minimum_sample_size():
    result = assess_performance_decay(
        {"sample_size": 10, "win_rate": 0.20, "profit_factor": 0.50}
    )

    assert result["health"] == "insufficient_data"
    assert result["recommended_stage"] == "keep_current"
    assert result["auto_transition_allowed"] is False


def test_decay_recommends_quarantine_for_severe_underperformance():
    result = assess_performance_decay(
        {
            "sample_size": 60,
            "win_rate": 0.30,
            "skill_score": 0.32,
            "profit_factor": 0.70,
        }
    )

    assert result["health"] == "critical"
    assert result["recommended_stage"] == "quarantined"
    assert result["auto_transition_allowed"] is False


def test_execute_returns_calibrated_confidence_and_decay_advisory(tmp_path, monkeypatch):
    client, skill = _client(tmp_path, monkeypatch, _DatabaseWithHistory())

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.0}},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] == "success"
    assert data["output"]["raw_confidence"] == 0.9
    assert data["output"]["calibrated_confidence"] == 0.675
    assert data["output"]["confidence"] == 0.675
    intelligence = data["performance_intelligence"]
    assert intelligence["status"] == "applied"
    assert intelligence["decay_assessment"]["health"] == "healthy"
    assert intelligence["auto_stage_transition"] is False


def test_execute_is_backward_compatible_without_history(tmp_path, monkeypatch):
    client, skill = _client(tmp_path, monkeypatch, _NoHistoryDatabase())

    response = client.post(
        f"/skills/{skill.skill_id}/execute",
        json={"inputs": {"price": 120.0}},
    )

    data = response.json()["data"]
    assert data["execution_status"] == "success"
    assert data["output"]["confidence"] == 0.9
    assert data["performance_intelligence"]["status"] == "no_history"
