import pytest

from app.executor import SafeSkillExecutor


@pytest.mark.parametrize(
    "attribute",
    ["__class__", "__bases__", "__subclasses__", "__globals__", "__mro__"],
)
def test_best_effort_executor_rejects_introspection_attributes_before_exec(attribute):
    code = f"def signal(value):\n    return {{'signal': str(value.{attribute})}}"

    result = SafeSkillExecutor().execute(
        skill_id="skill-escape",
        code=code,
        inputs={"value": 1},
    )

    assert result["execution_status"] == "rejected_unsafe_code"
    assert f"forbidden_introspection_attribute: {attribute}" in result["error"]
    assert result["output"] == {}
    assert result["isolation"] == "best_effort_not_a_true_sandbox"


def test_best_effort_executor_still_executes_plain_signal_code():
    result = SafeSkillExecutor().execute(
        skill_id="skill-plain",
        code="def signal(price):\n    return {'signal': 'buy', 'confidence': 0.8}",
        inputs={"price": 120.0},
    )

    assert result["execution_status"] == "success"
    assert result["output"] == {"signal": "buy", "confidence": 0.8}
    assert result["isolation"] == "best_effort_not_a_true_sandbox"
