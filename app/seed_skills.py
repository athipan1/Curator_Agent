from __future__ import annotations

import os

from app.models import SkillCreateRequest
from app.registry import SkillRegistry


DEFAULT_SKILL_CODE = """
def score_signal(final_score):
    if final_score >= 0.55:
        return {"result": "pass", "confidence": final_score, "reason": "score passed threshold"}
    return {"result": "observe", "confidence": final_score, "reason": "score below threshold"}
"""


def seed_default_backtest_skill(registry: SkillRegistry) -> dict:
    skill_id = os.getenv("CURATOR_SEED_BACKTEST_SKILL_ID", "hourly-sma-crossover")
    skill = registry.register(
        SkillCreateRequest(
            skill_id=skill_id,
            name=os.getenv("CURATOR_SEED_BACKTEST_SKILL_NAME", "Hourly Backtest Reference Skill"),
            description="Deterministic reference skill used to align Backtest_Agent stored results with Curator_Agent lifecycle.",
            code=DEFAULT_SKILL_CODE,
            tags=["backtest", "reference", "score"],
            market_context={"asset_class": "us_equity", "strategy_bucket": "value_rebound"},
            input_schema={"final_score": "float"},
            output_schema={"result": "str", "confidence": "float", "reason": "str"},
            source_agent="Curator_Agent",
        )
    )
    if os.getenv("CURATOR_SEED_BACKTEST_SKILL_APPROVED", "true").strip().lower() in {"1", "true", "yes", "y", "on"}:
        try:
            skill = registry.approve(
                skill.skill_id,
                approved_by="curator-seed",
                reason="deterministic backtest skill seed",
            )
        except ValueError:
            pass
    return skill.model_dump(mode="json")
