from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SkillCreateRequest
from app.registry import SkillRegistry


CODE_V1 = "def signal(value):\n    return {'signal': 'hold', 'confidence': 0.5}"
CODE_V2 = "def signal(value):\n    return {'signal': 'buy' if value > 0 else 'hold', 'confidence': 0.6}"


class _NoDatabase:
    enabled = False

    def get_skill_backtest_status(self, skill_id):
        return {"status": "skipped", "data": {"passed": False}}

    def rank_skills(self, **kwargs):
        return {"status": "skipped", "data": []}

    def create_skill_execution_log(self, payload, correlation_id=None):
        return {"status": "skipped", "data": {}}


def _client(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    app = create_app(registry=registry, database_client=_NoDatabase())
    return TestClient(app), registry


def _seed(registry):
    return registry.register(
        SkillCreateRequest(
            skill_id="skill-v1",
            skill_family_id="momentum-family",
            version="1.0.0",
            name="Momentum Signal",
            description="Versioned momentum signal.",
            code=CODE_V1,
            tags=["technical"],
            market_context={"asset_class": "us_equity", "regime": "momentum"},
        )
    )


def test_create_version_endpoint_preserves_lineage(tmp_path):
    client, registry = _client(tmp_path)
    parent = _seed(registry)

    response = client.post(
        f"/skills/{parent.skill_id}/versions",
        json={"version": "1.1.0", "code": CODE_V2},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    child = data["skill"]
    assert child["skill_family_id"] == "momentum-family"
    assert child["version"] == "1.1.0"
    assert child["parent_skill_id"] == parent.skill_id
    assert child["deployment_stage"] == "candidate"
    assert data["next_step"] == "validate_backtest_approve_then_promote"


def test_family_endpoints_show_all_versions_and_champion(tmp_path):
    client, registry = _client(tmp_path)
    first = _seed(registry)
    second = registry.create_version(
        first.skill_id,
        __import__("app.models", fromlist=["SkillVersionCreateRequest"]).SkillVersionCreateRequest(
            version="2.0.0",
            code=CODE_V2,
        ),
    )
    registry.approve(first.skill_id)
    registry.approve(second.skill_id)
    registry.promote(second.skill_id, deployment_stage="champion")

    detail = client.get("/skill-families/momentum-family")
    listing = client.get("/skill-families")

    assert detail.status_code == 200
    family = detail.json()["data"]
    assert family["version_count"] == 2
    assert family["champion_skill_id"] == second.skill_id
    assert family["champion_version"] == "2.0.0"
    assert listing.status_code == 200
    assert listing.json()["data"]["family_count"] == 1


def test_promote_endpoint_requires_validated_approved_skill(tmp_path):
    client, registry = _client(tmp_path)
    skill = _seed(registry)

    blocked = client.post(
        f"/skills/{skill.skill_id}/promote",
        json={"deployment_stage": "champion", "approved_by": "risk-owner"},
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "only_validated_approved_skills_can_be_deployed"

    registry.approve(skill.skill_id, approved_by="risk-owner")
    promoted = client.post(
        f"/skills/{skill.skill_id}/promote",
        json={
            "deployment_stage": "champion",
            "approved_by": "risk-owner",
            "reason": "paper observation passed",
        },
    )
    assert promoted.status_code == 200
    data = promoted.json()["data"]
    assert data["current_stage"] == "champion"
    assert data["family"]["champion_skill_id"] == skill.skill_id
    assert data["broker_access"] is False


def test_rollback_endpoint_restores_prior_version_as_champion(tmp_path):
    client, registry = _client(tmp_path)
    first = _seed(registry)
    from app.models import SkillVersionCreateRequest

    second = registry.create_version(
        first.skill_id,
        SkillVersionCreateRequest(version="2.0.0", code=CODE_V2),
    )
    registry.approve(first.skill_id)
    registry.approve(second.skill_id)
    registry.promote(first.skill_id, deployment_stage="champion")
    registry.promote(second.skill_id, deployment_stage="champion")

    response = client.post(
        f"/skills/{first.skill_id}/rollback",
        json={"approved_by": "risk-owner", "reason": "challenger degraded"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["rollback_status"] == "completed"
    assert data["restored_skill"]["skill_id"] == first.skill_id
    assert data["restored_skill"]["deployment_stage"] == "champion"
    assert data["replaced_champion_skill_id"] == second.skill_id
    assert data["family"]["champion_skill_id"] == first.skill_id
    assert registry.get(second.skill_id).deployment_stage == "retired"


def test_missing_family_and_skill_return_not_found(tmp_path):
    client, _registry = _client(tmp_path)

    assert client.get("/skill-families/missing").status_code == 404
    assert client.post(
        "/skills/missing/versions",
        json={"version": "1.1.0", "code": CODE_V2},
    ).status_code == 404
