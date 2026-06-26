from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from app.models import SkillCreateRequest, StandardResponse
from app.registry import SkillRegistry


DEFAULT_DB_PATH = os.getenv("CURATOR_DB_PATH", "./curator_skills.sqlite3")


def create_app(registry: SkillRegistry | None = None) -> FastAPI:
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    app = FastAPI(
        title="Curator Agent",
        version="0.1.0",
        description="Safe registry for reusable trading analysis skills. MVP stores and validates skills but does not execute them.",
    )

    @app.get("/health", response_model=StandardResponse)
    async def health() -> StandardResponse:
        return StandardResponse(
            data={
                "status": "healthy",
                "storage": "sqlite",
                "execution_enabled": False,
            }
        )

    @app.post("/skills/register", response_model=StandardResponse)
    async def register_skill(request: SkillCreateRequest) -> StandardResponse:
        record = skill_registry.register(request)
        return StandardResponse(data=record.model_dump(mode="json"))

    @app.get("/skills", response_model=StandardResponse)
    async def list_skills(
        tag: Optional[str] = Query(default=None),
        validation_status: Optional[str] = Query(default=None),
    ) -> StandardResponse:
        records = skill_registry.list(tag=tag, validation_status=validation_status)
        return StandardResponse(data=[record.model_dump(mode="json") for record in records])

    @app.get("/skills/search", response_model=StandardResponse)
    async def search_skills(q: str = Query(default="")) -> StandardResponse:
        records = skill_registry.search(q)
        return StandardResponse(data=[record.model_dump(mode="json") for record in records])

    @app.get("/skills/{skill_id}", response_model=StandardResponse)
    async def get_skill(skill_id: str) -> StandardResponse:
        try:
            record = skill_registry.get(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        return StandardResponse(data=record.model_dump(mode="json"))

    return app


app = create_app()
