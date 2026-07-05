from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class StandardResponse(BaseModel):
    status: str = "success"
    agent_type: str = "curator-agent"
    version: str = "0.1.0"
    data: Any


class SkillCreateRequest(BaseModel):
    skill_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=2000)
    code: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    market_context: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    source_agent: Optional[str] = Field(default=None, max_length=120)


class SkillLifecycleRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)
    approved_by: Optional[str] = Field(default=None, max_length=120)


class SkillBacktestApprovalRequest(SkillLifecycleRequest):
    require_backtest_passed: bool = True
    allow_missing_database: bool = False


class SkillBacktestStatusResponse(BaseModel):
    skill_id: str
    database_status: str
    passed: bool = False
    status: str = "unknown"
    latest_run_id: Optional[str] = None
    latest_score: Optional[float] = None
    latest_profit_factor: Optional[float] = None
    latest_win_rate: Optional[float] = None
    latest_max_drawdown: Optional[float] = None
    total_runs: int = 0
    reasons: List[str] = Field(default_factory=list)
    raw_response: Dict[str, Any] = Field(default_factory=dict)


class SkillExecuteRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    function_name: Optional[str] = Field(default=None, max_length=120)
    timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    account_id: str | int = 1
    symbol: Optional[str] = Field(default=None, max_length=20)
    strategy_bucket: Optional[str] = Field(default=None, max_length=120)
    market_regime: Optional[str] = Field(default=None, max_length=120)
    run_id: Optional[str] = Field(default=None, max_length=120)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillRecommendationRequest(BaseModel):
    account_id: str | int = 1
    symbol: Optional[str] = Field(default=None, max_length=20)
    asset_class: str = "us_equity"
    market_regime: Optional[str] = Field(default=None, max_length=120)
    strategy_bucket: Optional[str] = Field(default=None, max_length=120)
    timeframe: Optional[str] = Field(default=None, max_length=30)
    top_k: int = Field(default=3, ge=1, le=20)
    tags: List[str] = Field(default_factory=list)
    require_backtest_passed: bool = False


class RecommendedSkill(BaseModel):
    skill_id: str
    name: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    approval_status: str
    validation_status: str
    reason: str
    performance: Dict[str, Any] = Field(default_factory=dict)
    backtest_status: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    market_context: Dict[str, Any] = Field(default_factory=dict)


class SkillRecommendationResponse(BaseModel):
    recommendation_state: Literal["ranked", "fallback_no_database", "no_approved_skills", "no_backtest_passed_skills"]
    recommended_skills: List[RecommendedSkill] = Field(default_factory=list)
    rejected_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillRecord(BaseModel):
    skill_id: str
    name: str
    description: str
    code_hash: str
    tags: List[str]
    market_context: Dict[str, Any]
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    source_agent: Optional[str] = None
    validation_status: str
    approval_status: str
    validation_errors: List[str]
    lifecycle_notes: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SkillDetail(SkillRecord):
    code: str


class SkillValidationResult(BaseModel):
    approved: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class LearningPolicyDeltas(BaseModel):
    strategy_bucket_weights: Dict[str, float] = Field(default_factory=dict)
    asset_biases: Dict[str, float] = Field(default_factory=dict)
    risk: Dict[str, float] = Field(default_factory=dict)
    guardrails: Dict[str, Any] = Field(default_factory=dict)


class PerformanceLearningResult(BaseModel):
    learning_state: str = "success"
    learning_mode: str = "performance_summary_review"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reviewed_closed_plans: int = Field(default=0, ge=0)
    performance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    bucket_metrics: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    symbol_metrics: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    policy_deltas: LearningPolicyDeltas = Field(default_factory=LearningPolicyDeltas)
    reasoning: List[str] = Field(default_factory=list)


class PerformancePolicyCurationRequest(BaseModel):
    account_id: str | int
    learning_result: PerformanceLearningResult
    current_policy: Optional[Dict[str, Any]] = None
    min_confidence_to_apply: float = Field(default=0.70, ge=0.0, le=1.0)
    pause_threshold: float = Field(default=-0.10, ge=-1.0, le=0.0)


class CuratedPolicyAction(BaseModel):
    target_type: Literal["strategy_bucket", "symbol", "risk", "guardrail"]
    target: str
    action: Literal[
        "increase_weight",
        "decrease_weight",
        "pause_strategy_bucket",
        "increase_bias",
        "decrease_bias",
        "reduce_risk",
        "require_human_review",
        "keep_observing",
    ]
    delta: Optional[float] = None
    auto_apply: bool = False
    priority: Literal["low", "medium", "high"] = "medium"
    reason: str


class PerformancePolicyCurationResponse(BaseModel):
    curation_state: Literal["approved_for_review", "review_required", "observation_only", "rejected"]
    action_count: int = 0
    actions: List[CuratedPolicyAction] = Field(default_factory=list)
    rejected_actions: List[CuratedPolicyAction] = Field(default_factory=list)
    reasoning: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
