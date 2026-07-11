import pytest

from app.models import SkillCreateRequest, SkillRecommendationRequest, SkillVersionCreateRequest
from app.recommendations import recommend_skills
from app.registry import SkillRegistry


CODE_V1 = "def signal(value):\n    return {'signal': 'hold', 'confidence': 0.5}"
CODE_V2 = "def signal(value):\n    return {'signal': 'buy' if value > 0 else 'hold', 'confidence': 0.6}"


class _NoDatabase:
    enabled = False

    def rank_skills(self, **kwargs):
        return {"status": "skipped", "data": []}

    def get_skill_backtest_status(self, skill_id):
        return {"status": "skipped", "data": {"passed": False}}


def _request(**overrides):
    payload = {
        "name": "Versioned Signal",
        "description": "A deterministic signal-only skill.",
        "code": CODE_V1,
        "tags": ["technical"],
        "market_context": {"asset_class": "us_equity", "regime": "momentum"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    payload.update(overrides)
    return SkillCreateRequest(**payload)


def test_legacy_registration_gets_version_family_and_candidate_stage(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))

    skill = registry.register(_request(skill_id="skill-v1"))

    assert skill.skill_family_id == "skill-v1"
    assert skill.version == "1.0.0"
    assert skill.parent_skill_id is None
    assert skill.deployment_stage == "candidate"
    assert skill.immutable is False


def test_skill_id_cannot_be_reused_with_different_code(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    registry.register(_request(skill_id="skill-v1"))

    with pytest.raises(ValueError, match="immutable_skill_id_code_hash_mismatch"):
        registry.register(_request(skill_id="skill-v1", code=CODE_V2))


def test_create_version_inherits_family_and_links_parent(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    parent = registry.register(_request(skill_id="skill-v1", skill_family_id="signal-family"))

    child = registry.create_version(
        parent.skill_id,
        SkillVersionCreateRequest(version="1.1.0", code=CODE_V2),
    )

    assert child.skill_id != parent.skill_id
    assert child.skill_family_id == "signal-family"
    assert child.version == "1.1.0"
    assert child.parent_skill_id == parent.skill_id
    assert child.code_hash != parent.code_hash
    assert child.deployment_stage == "candidate"


def test_family_version_cannot_point_to_different_code(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    registry.register(
        _request(
            skill_id="skill-v1",
            skill_family_id="signal-family",
            version="1.0.0",
        )
    )

    with pytest.raises(
        ValueError,
        match="skill_family_version_already_exists_with_different_code",
    ):
        registry.register(
            _request(
                skill_id="another-id",
                skill_family_id="signal-family",
                version="1.0.0",
                code=CODE_V2,
            )
        )


def test_approval_locks_version_and_champion_replaces_prior_champion(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    first = registry.register(
        _request(skill_id="skill-v1", skill_family_id="signal-family")
    )
    second = registry.create_version(
        first.skill_id,
        SkillVersionCreateRequest(version="2.0.0", code=CODE_V2),
    )

    first = registry.approve(first.skill_id, approved_by="risk-owner")
    second = registry.approve(second.skill_id, approved_by="risk-owner")
    first = registry.promote(
        first.skill_id,
        deployment_stage="champion",
        approved_by="risk-owner",
    )
    second = registry.promote(
        second.skill_id,
        deployment_stage="champion",
        approved_by="risk-owner",
    )

    assert first.immutable is True
    assert registry.get(first.skill_id).deployment_stage == "retired"
    assert registry.get(second.skill_id).deployment_stage == "champion"
    champions = registry.list(
        skill_family_id="signal-family",
        deployment_stage="champion",
    )
    assert [skill.skill_id for skill in champions] == [second.skill_id]


def test_recommendations_exclude_retired_and_prefer_champion(tmp_path):
    registry = SkillRegistry(str(tmp_path / "curator.sqlite3"))
    first = registry.register(
        _request(skill_id="skill-v1", skill_family_id="signal-family")
    )
    second = registry.create_version(
        first.skill_id,
        SkillVersionCreateRequest(version="2.0.0", code=CODE_V2),
    )
    registry.approve(first.skill_id)
    registry.approve(second.skill_id)
    registry.promote(first.skill_id, deployment_stage="champion")
    registry.promote(second.skill_id, deployment_stage="champion")

    result = recommend_skills(
        registry=registry,
        database_client=_NoDatabase(),
        request=SkillRecommendationRequest(
            tags=["technical"],
            market_regime="momentum",
            top_k=5,
        ),
    )

    assert len(result.recommended_skills) == 1
    selected = result.recommended_skills[0]
    assert selected.skill_id == second.skill_id
    assert selected.skill_family_id == "signal-family"
    assert selected.version == "2.0.0"
    assert selected.deployment_stage == "champion"
    assert result.metadata["version_aware"] is True
