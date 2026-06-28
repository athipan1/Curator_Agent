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


class SkillExecuteRequest(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    function_name: Optional[str] = Field(default=None, max_length=120)
    timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)


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
