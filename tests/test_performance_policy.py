from fastapi.testclient import TestClient

from app.main import create_app
from app.models import PerformancePolicyCurationRequest
from app.performance_policy import curate_performance_policy


def learning_result_payload():
    return {
        "learning_state": "success",
        "learning_mode": "performance_summary_review",
        "confidence_score": 0.82,
        "reviewed_closed_plans": 30,
        "performance_score": 0.74,
        "policy_deltas": {
            "strategy_bucket_weights": {
                "value_rebound": 0.05,
                "news_momentum": -0.05,
            },
            "asset_biases": {
                "AAPL": 0.03,
                "MSFT": -0.03,
            },
            "risk": {
                "risk_per_trade": -0.0025,
            },
            "guardrails": {
                "auto_apply": False,
                "requires_human_review": True,
            },
        },
        "reasoning": ["Learning_Agent reviewed performance summary."],
    }


def test_curate_performance_policy_requires_human_review():
    request = PerformancePolicyCurationRequest(
        account_id="1",
        learning_result=learning_result_payload(),
    )

    response = curate_performance_policy(request)

    assert response.curation_state == "review_required"
    actions = {(action.target_type, action.target): action for action in response.actions}
    assert actions[("strategy_bucket", "value_rebound")].action == "increase_weight"
    assert actions[("strategy_bucket", "news_momentum")].action == "decrease_weight"
    assert actions[("symbol", "AAPL")].action == "increase_bias"
    assert actions[("symbol", "MSFT")].action == "decrease_bias"
    assert actions[("risk", "risk_per_trade")].action == "reduce_risk"
    assert actions[("guardrail", "human_review")].action == "require_human_review"
    assert all(action.auto_apply is False for action in response.actions)


def test_curate_performance_policy_warmup_is_observation_only():
    payload = learning_result_payload()
    payload["learning_state"] = "warmup"
    payload["reviewed_closed_plans"] = 2
    request = PerformancePolicyCurationRequest(account_id="1", learning_result=payload)

    response = curate_performance_policy(request)

    assert response.curation_state == "observation_only"
    assert response.action_count == 0
    assert "observation-only" in response.reasoning[0]


def test_curate_performance_policy_pauses_large_negative_bucket_delta():
    payload = learning_result_payload()
    payload["policy_deltas"]["strategy_bucket_weights"] = {"news_momentum": -0.15}
    request = PerformancePolicyCurationRequest(account_id="1", learning_result=payload, pause_threshold=-0.10)

    response = curate_performance_policy(request)

    bucket_action = next(action for action in response.actions if action.target == "news_momentum")
    assert bucket_action.action == "pause_strategy_bucket"
    assert bucket_action.priority == "high"
    assert bucket_action.auto_apply is False


def test_curate_performance_policy_allows_auto_apply_when_guardrails_allow_it():
    payload = learning_result_payload()
    payload["policy_deltas"]["guardrails"] = {
        "auto_apply": True,
        "requires_human_review": False,
    }
    payload["policy_deltas"]["risk"] = {}
    request = PerformancePolicyCurationRequest(account_id="1", learning_result=payload, min_confidence_to_apply=0.70)

    response = curate_performance_policy(request)

    assert response.curation_state == "approved_for_review"
    assert any(action.auto_apply for action in response.actions)
    assert not any(action.action == "require_human_review" for action in response.actions)


def test_curate_performance_policy_endpoint(tmp_path):
    client = TestClient(create_app())

    response = client.post(
        "/curate/performance-policy",
        json={
            "account_id": "1",
            "learning_result": learning_result_payload(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["curation_state"] == "review_required"
    assert payload["data"]["action_count"] >= 1
    assert any(action["action"] == "require_human_review" for action in payload["data"]["actions"])
