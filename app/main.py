from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from app.executor import SafeSkillExecutor
from app.models import (
    PerformancePolicyCurationRequest,
    SkillCreateRequest,
    SkillExecuteRequest,
    SkillLifecycleRequest,
    StandardResponse,
)
from app.performance_policy import curate_performance_policy
from app.registry import APPROVAL_APPROVED, SkillRegistry


DEFAULT_DB_PATH = os.getenv("CURATOR_DB_PATH", "./curator_skills.sqlite3")


def create_app(
    registry: SkillRegistry | None = None,
    executor: SafeSkillExecutor | None = None,
) -> FastAPI:
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    skill_executor = executor or SafeSkillExecutor()
    app = FastAPI(
        title="Curator Agent",
        version="0.2.0",
        description="Safe registry and sandbox runner for reusable trading analysis skills.",
    )

    @app.get("/health", response_model=StandardResponse)
    async def health() -> StandardResponse:
        return StandardResponse(
            data={
                "status": "healthy",
                "storage": "sqlite",
                "execution_enabled": True,
                "execution_mode": "restricted_process_signal_only",
            }
        )

    @app.post("/curate/performance-policy", response_model=StandardResponse)
    async def curate_performance_policy_endpoint(request: PerformancePolicyCurationRequest) -> StandardResponse:
        result = curate_performance_policy(request)
        return StandardResponse(data=result.model_dump(mode="json"))

    @app.post("/skills/register", response_model=StandardResponse)
    async def register_skill(request: SkillCreateRequest) -> StandardResponse:
        record = skill_registry.register(request)
        return StandardResponse(data=record.model_dump(mode="json"))

    @app.get("/skills", response_model=StandardResponse)
    async def list_skills(
        tag: Optional[str] = Query(default=None),
        validation_status: Optional[str] = Query(default=None),
        approval_status: Optional[str] = Query(default=None),
    ) -> StandardResponse:
        records = skill_registry.list(
            tag=tag,
            validation_status=validation_status,
            approval_status=approval_status,
        )
        return StandardResponse(data=[record.model_dump(mode="json") for record in records])

    @app.get("/skills/search", response_model=StandardResponse)
    async def search_skills(
        q: str = Query(default=""),
        approval_status: Optional[str] = Query(default=None),
    ) -> StandardResponse:
        records = skill_registry.search(q, approval_status=approval_status)
        return StandardResponse(data=[record.model_dump(mode="json") for record in records])

    @app.post("/skills/{skill_id}/approve", response_model=StandardResponse)
    async def approve_skill(skill_id: str, request: SkillLifecycleRequest) -> StandardResponse:
        try:
            record = skill_registry.approve(
                skill_id,
                approved_by=request.approved_by,
                reason=request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(data=record.model_dump(mode="json"))

    @app.post("/skills/{skill_id}/deprecate", response_model=StandardResponse)
    async def deprecate_skill(skill_id: str, request: SkillLifecycleRequest) -> StandardResponse:
        try:
            record = skill_registry.deprecate(
                skill_id,
                approved_by=request.approved_by,
                reason=request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        return StandardResponse(data=record.model_dump(mode="json"))

    @app.post("/skills/{skill_id}/execute", response_model=StandardResponse)
    async def execute_skill(skill_id: str, request: SkillExecuteRequest) -> StandardResponse:
        try:
            record = skill_registry.get(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc

        if record.validation_status != "validated":
            raise HTTPException(status_code=400, detail="only_validated_skills_can_execute")
        if record.approval_status != APPROVAL_APPROVED:
            raise HTTPException(status_code=400, detail="only_approved_skills_can_execute")

        result = skill_executor.execute(
            skill_id=record.skill_id,
            code=record.code,
            inputs=request.inputs,
            function_name=request.function_name,
            timeout_seconds=request.timeout_seconds,
        )
        return StandardResponse(data=result)

    @app.get("/skills/{skill_id}", response_model=StandardResponse)
    async def get_skill(skill_id: str) -> StandardResponse:
        try:
            record = skill_registry.get(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        return StandardResponse(data=record.model_dump(mode="json"))

    return app


app = create_app()
