from fastapi.testclient import TestClient

from app.main import create_app
from app.registry import SkillRegistry


VALID_SKILL = """
def score_signal(final_score):
    if final_score >= 0.55:
        return {"signal": "buy", "confidence": final_score, "reason": "score passed threshold"}
    return {"signal": "hold", "confidence": final_score, "reason": "score below threshold"}
"""


class FakeDatabaseClient:
    enabled = True

    def __init__(self, *, passed=True, status="backtest_passed"):
        self.passed = passed
        self.status = status
        self.requested_skill_ids = []

    def create_skill_execution_log(self, payload):
        return {"status": "success", "data": {"execution_log_id": "log-1"}}

    def rank_skills(self, **kwargs):
        return {"status": "success", "data": []}

    def get_skill_backtest_status(self, skill_id):
        self.requested_skill_ids.append(skill_id)
        return {
            "status": "success",
            "data": {
                "skill_id": skill_id,
                "status": self.status,
                "passed": self.passed,
                "latest_run_id": "run-1" if self.passed else None,
                "latest_score": 0.81 if self.passed else None,
                "latest_profit_factor": 1.45 if self.passed else None,
                "latest_win_rate": 0.55 if self.passed else None,
                "latest_max_drawdown": -0.08 if self.passed else None,
                "total_runs": 1 if self.passed else 0,
                "reasons": ["fixture"],
            },
        }


def _register_valid_skill(client: TestClient) -> dict:
    response = client.post(
        "/skills/register",
        json={
            "name": "Manager Advisory Score Signal",
            "description": "Uses Manager final score to produce advisory signal.",
            "code": VALID_SKILL,
            "tags": ["manager", "advisory"],
            "market_context": {"asset_class": "us_equity", "strategy_bucket": "value_rebound"},
            "input_schema": {"final_score": "float"},
            "output_schema": {"signal": "str", "confidence": "float", "reason": "str"},
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_get_skill_backtest_status_reads_database_agent(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    database_client = FakeDatabaseClient(passed=True)
    client = TestClient(create_app(registry=registry, database_client=database_client))
    skill = _register_valid_skill(client)

    response = client.get(f"/skills/{skill['skill_id']}/backtest-status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_id"] == skill["skill_id"]
    assert data["passed"] is True
    assert data["latest_run_id"] == "run-1"
    assert database_client.requested_skill_ids == [skill["skill_id"]]


def test_approve_from_backtest_requires_passed_status(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    database_client = FakeDatabaseClient(passed=False, status="not_backtested")
    client = TestClient(create_app(registry=registry, database_client=database_client))
    skill = _register_valid_skill(client)

    response = client.post(
        f"/skills/{skill['skill_id']}/approve-from-backtest",
        json={"approved_by": "risk-owner", "reason": "require backtest"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "skill_backtest_not_passed"


def test_approve_from_backtest_approves_when_passed(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    database_client = FakeDatabaseClient(passed=True)
    client = TestClient(create_app(registry=registry, database_client=database_client))
    skill = _register_valid_skill(client)

    response = client.post(
        f"/skills/{skill['skill_id']}/approve-from-backtest",
        json={"approved_by": "risk-owner", "reason": "backtest passed"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill"]["approval_status"] == "approved"
    assert data["backtest_status"]["passed"] is True
    assert data["skill"]["lifecycle_notes"][-1]["action"] == "approved"


def test_recommend_can_require_backtest_passed(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    database_client = FakeDatabaseClient(passed=False, status="not_backtested")
    client = TestClient(create_app(registry=registry, database_client=database_client))
    skill = _register_valid_skill(client)
    client.post(f"/skills/{skill['skill_id']}/approve", json={})

    response = client.post(
        "/skills/recommend",
        json={
            "strategy_bucket": "value_rebound",
            "tags": ["manager"],
            "require_backtest_passed": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recommendation_state"] == "no_backtest_passed_skills"
    assert data["recommended_skills"] == []
    assert data["rejected_count"] == 1


def test_recommend_includes_backtest_status_when_available(tmp_path):
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))
    database_client = FakeDatabaseClient(passed=True)
    client = TestClient(create_app(registry=registry, database_client=database_client))
    skill = _register_valid_skill(client)
    client.post(f"/skills/{skill['skill_id']}/approve", json={})

    response = client.post(
        "/skills/recommend",
        json={
            "strategy_bucket": "value_rebound",
            "tags": ["manager"],
            "require_backtest_passed": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recommendation_state"] == "fallback_no_database"
    assert data["recommended_skills"][0]["skill_id"] == skill["skill_id"]
    assert data["recommended_skills"][0]["backtest_status"]["passed"] is True
