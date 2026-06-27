from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

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
