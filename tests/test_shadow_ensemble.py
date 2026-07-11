from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SkillCreateRequest
from app.registry import SkillRegistry


BUY_CODE = "def signal(price):\n    return {'signal': 'buy', 'confidence': 0.8, 'reason': 'buy'}"
HOLD_CODE = "def signal(price):\n    return {'signal': 'hold', 'confidence': 0.6, 'reason': 'hold'}"
SELL_CODE = "def signal(price):\n    return {'signal': 'sell', 'confidence': 0.7, 'reason': 'sell'}"


class _NoHistoryDatabase:
    enabled = False

    def rank_skills(self, **kwargs):
        return {"status": "skipped", "data": []}

    def get_skill_backtest_status(self, skill_id):
        return {"status": "skipped", "data": {"passed": False}}

    def create_skill_execution_log(self, payload, correlation_id=None):
        return {"status": "skipped", "data": {}}


def _register(registry, *, skill_id, family, stage, code, group):
    skill = registry.register(
        SkillCreateRequest(
            skill_id=skill_id,
            skill_family_id=family,
            name=skill_id,
            description="Shadow ensemble test skill.",
            code=code,
            market_context={"correlation_group": group},
        )
    )
    registry.approve(skill.skill_id)
    return registry.promote(skill.skill_id, deployment_stage=stage)


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_SEED_BACKTEST_SKILL", "false")
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    app = create_app(registry=registry, database_client=_NoHistoryDatabase())
    return TestClient(app), registry


def test_shadow_ensemble_prefers_diverse_groups_and_returns_consensus(tmp_path, monkeypatch):
    client, registry = _client(tmp_path, monkeypatch)
    _register(
        registry,
        skill_id="champion-buy",
        family="momentum-family",
        stage="champion",
        code=BUY_CODE,
        group="technical",
    )
    _register(
        registry,
        skill_id="challenger-buy",
        family="value-family",
        stage="challenger",
        code=BUY_CODE,
        group="fundamental",
    )
    _register(
        registry,
        skill_id="shadow-hold",
        family="regime-family",
        stage="shadow",
        code=HOLD_CODE,
        group="regime",
    )

    response = client.post(
        "/skills/shadow-ensemble",
        json={"inputs": {"price": 120.0}, "minimum_agreement": 0.6},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_mode"] == "shadow_ensemble"
    assert data["advisory_only"] is True
    assert data["broker_access"] is False
    assert data["order_placement"] is False
    assert data["selected_skill_count"] == 3
    assert data["diversity_group_count"] == 3
    assert data["diversity_score"] == 1.0
    assert data["consensus"]["signal"] == "buy"
    assert data["consensus"]["state"] == "consensus"
    assert data["manager_contract"]["direct_execution_allowed"] is False


def test_low_agreement_fails_closed_to_hold(tmp_path, monkeypatch):
    client, registry = _client(tmp_path, monkeypatch)
    _register(
        registry,
        skill_id="champion-buy",
        family="momentum-family",
        stage="champion",
        code=BUY_CODE,
        group="technical",
    )
    _register(
        registry,
        skill_id="challenger-sell",
        family="value-family",
        stage="challenger",
        code=SELL_CODE,
        group="fundamental",
    )

    response = client.post(
        "/skills/shadow-ensemble",
        json={"inputs": {"price": 120.0}, "minimum_agreement": 0.75},
    )

    data = response.json()["data"]
    assert data["consensus"]["state"] == "insufficient_agreement"
    assert data["consensus"]["signal"] == "hold"
    assert data["consensus"]["leading_signal"] in {"buy", "sell"}


def test_candidate_and_retired_skills_are_excluded(tmp_path, monkeypatch):
    client, registry = _client(tmp_path, monkeypatch)
    candidate = registry.register(
        SkillCreateRequest(
            skill_id="candidate",
            name="candidate",
            description="Candidate skill.",
            code=BUY_CODE,
        )
    )
    registry.approve(candidate.skill_id)
    retired = _register(
        registry,
        skill_id="retired",
        family="retired-family",
        stage="shadow",
        code=SELL_CODE,
        group="old",
    )
    registry.promote(retired.skill_id, deployment_stage="retired")
    _register(
        registry,
        skill_id="champion",
        family="active-family",
        stage="champion",
        code=BUY_CODE,
        group="technical",
    )

    response = client.post(
        "/skills/shadow-ensemble",
        json={"inputs": {"price": 120.0}},
    )

    executions = response.json()["data"]["executions"]
    assert [item["skill_id"] for item in executions] == ["champion"]


def test_no_eligible_skills_returns_not_found(tmp_path, monkeypatch):
    client, _registry = _client(tmp_path, monkeypatch)

    response = client.post(
        "/skills/shadow-ensemble",
        json={"inputs": {"price": 120.0}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "no_eligible_shadow_ensemble_skills"
