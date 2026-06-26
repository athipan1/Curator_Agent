from app.validator import SafeSkillValidator


def test_validator_accepts_pure_function_skill():
    code = """
def rsi_signal(close_values):
    return {"signal": "hold", "confidence": 0.5, "close_count": len(close_values)}
"""

    result = SafeSkillValidator().validate(code)

    assert result.approved is True
    assert result.errors == []


def test_validator_rejects_imports_and_file_access():
    code = """
import os

def bad_skill():
    return open('/tmp/secret').read()
"""

    result = SafeSkillValidator().validate(code)

    assert result.approved is False
    assert "forbidden_ast_node: Import" in result.errors
    assert "forbidden_call: open" in result.errors


def test_validator_rejects_code_without_function():
    result = SafeSkillValidator().validate("x = 1 + 1")

    assert result.approved is False
    assert "skill_must_define_at_least_one_function" in result.errors
