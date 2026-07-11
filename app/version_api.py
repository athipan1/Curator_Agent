from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict

from fastapi import APIRouter, FastAPI, HTTPException

from app.models import (
    SkillLifecycleRequest,
    SkillPromotionRequest,
    SkillVersionCreateRequest,
    StandardResponse,
)
from app.registry import DEPLOYMENT_CHAMPION, SkillRegistry


def _record_data(record: Any) -> Dict[str, Any]:
    return record.model_dump(mode="json")


def _family_summary(registry: SkillRegistry, family_id: str) -> Dict[str, Any]:
    versions = registry.list(skill_family_id=family_id)
    if not versions:
        raise KeyError(family_id)
    ordered = sorted(
        versions,
        key=lambda item: (item.created_at, item.version, item.skill_id),
    )
    champions = [
        item for item in ordered if item.deployment_stage == DEPLOYMENT_CHAMPION
    ]
    latest = ordered[-1]
    return {
        "skill_family_id": family_id,
        "name": latest.name,
        "version_count": len(ordered),
        "champion_skill_id": champions[-1].skill_id if champions else None,
        "champion_version": champions[-1].version if champions else None,
        "latest_skill_id": latest.skill_id,
        "latest_version": latest.version,
        "versions": [_record_data(item) for item in ordered],
    }


def build_version_lifecycle_router(registry: SkillRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/skill-families", response_model=StandardResponse)
    async def list_skill_families() -> StandardResponse:
        grouped: Dict[str, list[Any]] = defaultdict(list)
        for skill in registry.list():
            grouped[skill.skill_family_id].append(skill)
        families = [
            _family_summary(registry, family_id)
            for family_id in sorted(grouped)
        ]
        return StandardResponse(
            data={
                "family_count": len(families),
                "families": families,
                "versioning_enabled": True,
            }
        )

    @router.get(
        "/skill-families/{skill_family_id}",
        response_model=StandardResponse,
    )
    async def get_skill_family(skill_family_id: str) -> StandardResponse:
        try:
            family = _family_summary(registry, skill_family_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="skill_family_not_found",
            ) from exc
        return StandardResponse(data=family)

    @router.post(
        "/skills/{skill_id}/versions",
        response_model=StandardResponse,
    )
    async def create_skill_version(
        skill_id: str,
        request: SkillVersionCreateRequest,
    ) -> StandardResponse:
        try:
            record = registry.create_version(skill_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(
            data={
                "skill": _record_data(record),
                "created_from_skill_id": skill_id,
                "immutable_parent": registry.get(skill_id).immutable,
                "next_step": "validate_backtest_approve_then_promote",
            }
        )

    @router.post(
        "/skills/{skill_id}/promote",
        response_model=StandardResponse,
    )
    async def promote_skill(
        skill_id: str,
        request: SkillPromotionRequest,
    ) -> StandardResponse:
        try:
            before = registry.get(skill_id)
            record = registry.promote(
                skill_id,
                deployment_stage=request.deployment_stage,
                approved_by=request.approved_by,
                reason=request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(
            data={
                "skill": _record_data(record),
                "previous_stage": before.deployment_stage,
                "current_stage": record.deployment_stage,
                "family": _family_summary(registry, record.skill_family_id),
                "advisory_only": True,
                "broker_access": False,
            }
        )

    @router.post(
        "/skills/{skill_id}/rollback",
        response_model=StandardResponse,
    )
    async def rollback_family_to_skill(
        skill_id: str,
        request: SkillLifecycleRequest,
    ) -> StandardResponse:
        try:
            target = registry.get(skill_id)
            prior_champion = next(
                (
                    item
                    for item in registry.list(
                        skill_family_id=target.skill_family_id,
                        deployment_stage=DEPLOYMENT_CHAMPION,
                    )
                    if item.skill_id != target.skill_id
                ),
                None,
            )
            restored = registry.promote(
                target.skill_id,
                deployment_stage=DEPLOYMENT_CHAMPION,
                approved_by=request.approved_by,
                reason=request.reason or "manual_champion_rollback",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(
            data={
                "rollback_status": "completed",
                "restored_skill": _record_data(restored),
                "replaced_champion_skill_id": (
                    prior_champion.skill_id if prior_champion else None
                ),
                "family": _family_summary(registry, restored.skill_family_id),
                "advisory_only": True,
                "broker_access": False,
            }
        )

    return router


def attach_version_lifecycle_routes(
    app: FastAPI,
    registry: SkillRegistry,
) -> FastAPI:
    app.include_router(build_version_lifecycle_router(registry))
    app.state.skill_registry = registry
    app.state.skill_versioning_enabled = True
    return app
