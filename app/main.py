from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query

from app.database_client import DatabaseAgentClient
from app.executor import SafeSkillExecutor
from app.models import (
    PerformancePolicyCurationRequest,
    SkillBacktestApprovalRequest,
    SkillBacktestStatusResponse,
    SkillCreateRequest,
    SkillExecuteRequest,
    SkillLifecycleRequest,
    SkillRecommendationRequest,
    StandardResponse,
)
from app.performance_policy import curate_performance_policy
from app.recommendations import recommend_skills
from app.registry import APPROVAL_APPROVED, SkillRegistry
from app.seed_skills import seed_default_backtest_skill
from app.system_contract import (
    CURATOR_AGENT_TYPE,
    CURATOR_AGENT_VERSION,
    CURATOR_SERVICE_VERSION,
    SCHEMA_VERSION,
    contract_response,
)


DEFAULT_DB_PATH = os.getenv("CURATOR_DB_PATH", "./curator_skills.sqlite3")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _backtest_status_from_database(skill_id: str, database_client: DatabaseAgentClient) -> SkillBacktestStatusResponse:
    response = database_client.get_skill_backtest_status(skill_id)
    data = response.get("data") if isinstance(response, dict) else None
    data = data if isinstance(data, dict) else {}
    return SkillBacktestStatusResponse(
        skill_id=skill_id,
        database_status=str(response.get("status", "unknown")) if isinstance(response, dict) else "unknown",
        passed=bool(data.get("passed")),
        status=str(data.get("status", "unknown")),
        latest_run_id=data.get("latest_run_id"),
        latest_score=data.get("latest_score"),
        latest_profit_factor=data.get("latest_profit_factor"),
        latest_win_rate=data.get("latest_win_rate"),
        latest_max_drawdown=data.get("latest_max_drawdown"),
        total_runs=int(data.get("total_runs") or 0),
        reasons=data.get("reasons") if isinstance(data.get("reasons"), list) else [],
        raw_response=response if isinstance(response, dict) else {},
    )


def create_app(
    registry: SkillRegistry | None = None,
    executor: SafeSkillExecutor | None = None,
    database_client: DatabaseAgentClient | None = None,
) -> FastAPI:
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    skill_executor = executor or SafeSkillExecutor()
    skill_database_client = database_client or DatabaseAgentClient()
    seeded_skill: Dict[str, Any] | None = None
    if _bool_env("CURATOR_SEED_BACKTEST_SKILL", True):
        seeded_skill = seed_default_backtest_skill(skill_registry)
    app = FastAPI(
        title="Curator Agent",
        version=CURATOR_SERVICE_VERSION,
        description="Safe registry and sandbox runner for reusable trading analysis skills.",
    )

    @app.get("/version")
    async def version() -> Dict[str, Any]:
        return contract_response(
            status="success",
            data={
                "agent_type": CURATOR_AGENT_TYPE,
                "version": CURATOR_AGENT_VERSION,
                "service_version": CURATOR_SERVICE_VERSION,
                "schema_version": SCHEMA_VERSION,
                "api_contract": "multi-agent-trading-api-contract",
            },
            metadata={
                "required_operational_endpoints": ["/health", "/ready", "/version"],
            },
        )

    @app.get("/ready")
    async def ready() -> Dict[str, Any]:
        return contract_response(
            status="success",
            data={
                "ready": True,
                "storage": "sqlite",
                "execution_enabled": True,
                "database_telemetry_enabled": skill_database_client.enabled,
                "database_backtest_status_enabled": skill_database_client.enabled,
                "seeded_backtest_skill_id": (seeded_skill or {}).get("skill_id"),
                "performance_policy_endpoint": "/curate/performance-policy",
                "skill_register_endpoint": "/skills/register",
                "skill_list_endpoint": "/skills",
                "skill_search_endpoint": "/skills/search",
                "skill_recommend_endpoint": "/skills/recommend",
                "skill_backtest_status_endpoint": "/skills/{skill_id}/backtest-status",
                "skill_approve_from_backtest_endpoint": "/skills/{skill_id}/approve-from-backtest",
                "skill_execute_endpoint": "/skills/{skill_id}/execute",
            },
            metadata={
                "contract_source": "curator-agent-runtime-contract",
            },
            confidence_score=1.0,
        )

    @app.get("/health", response_model=StandardResponse)
    async def health() -> StandardResponse:
        return StandardResponse(
            data={
                "status": "healthy",
                "storage": "sqlite",
                "execution_enabled": True,
                "execution_mode": "restricted_process_signal_only",
                "database_telemetry_enabled": skill_database_client.enabled,
                "database_backtest_status_enabled": skill_database_client.enabled,
                "seeded_backtest_skill_id": (seeded_skill or {}).get("skill_id"),
            }
        )

    @app.post("/curate/performance-policy", response_model=StandardResponse)
    async def curate_performance_policy_endpoint(
        request: PerformancePolicyCurationRequest,
    ) -> StandardResponse:
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

    @app.post("/skills/recommend", response_model=StandardResponse)
    async def recommend_skills_endpoint(request: SkillRecommendationRequest) -> StandardResponse:
        result = recommend_skills(
            registry=skill_registry,
            database_client=skill_database_client,
            request=request,
        )
        return StandardResponse(data=result.model_dump(mode="json"))

    @app.post("/skills/{skill_id}/approve", response_model=StandardResponse)
    async def approve_skill(
        skill_id: str,
        request: SkillLifecycleRequest,
    ) -> StandardResponse:
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

    @app.get("/skills/{skill_id}/backtest-status", response_model=StandardResponse)
    async def skill_backtest_status(skill_id: str) -> StandardResponse:
        try:
            skill_registry.get(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc
        status = _backtest_status_from_database(skill_id, skill_database_client)
        return StandardResponse(data=status.model_dump(mode="json"))

    @app.post("/skills/{skill_id}/approve-from-backtest", response_model=StandardResponse)
    async def approve_skill_from_backtest(
        skill_id: str,
        request: SkillBacktestApprovalRequest,
    ) -> StandardResponse:
        try:
            skill_registry.get(skill_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="skill_not_found") from exc

        status = _backtest_status_from_database(skill_id, skill_database_client)
        if request.require_backtest_passed and not status.passed:
            if request.allow_missing_database and status.database_status == "skipped":
                pass
            else:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "skill_backtest_not_passed",
                        "backtest_status": status.model_dump(mode="json"),
                    },
                )
        try:
            record = skill_registry.approve(
                skill_id,
                approved_by=request.approved_by,
                reason=request.reason or f"backtest_status={status.status}; latest_run_id={status.latest_run_id}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(
            data={
                "skill": record.model_dump(mode="json"),
                "backtest_status": status.model_dump(mode="json"),
                "advisory_only": True,
            }
        )

    @app.post("/skills/{skill_id}/deprecate", response_model=StandardResponse)
    async def deprecate_skill(
        skill_id: str,
        request: SkillLifecycleRequest,
    ) -> StandardResponse:
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
        output = result.get("output") if isinstance(result, dict) else {}
        output = output if isinstance(output, dict) else {}
        telemetry_payload = {
            "account_id": request.account_id,
            "skill_id": record.skill_id,
            "skill_name": record.name,
            "symbol": request.symbol,
            "strategy_bucket": request.strategy_bucket,
            "market_regime": request.market_regime,
            "signal": output.get("signal"),
            "confidence": output.get("confidence"),
            "reason": output.get("reason"),
            "input_payload": request.inputs,
            "output_payload": output,
            "execution_status": result.get("execution_status", "unknown"),
            "error": result.get("error"),
            "elapsed_ms": result.get("elapsed_ms"),
            "source_agent": "curator-agent",
            "run_id": request.run_id,
            "metadata": {
                **request.metadata,
                "function_name": result.get("function_name"),
                "code_hash": record.code_hash,
                "advisory_only": True,
            },
        }
        telemetry_result = skill_database_client.create_skill_execution_log(telemetry_payload)
        result["database_telemetry"] = {
            "status": telemetry_result.get("status"),
            "enabled": skill_database_client.enabled,
            "execution_log_id": (telemetry_result.get("data") or {}).get("execution_log_id")
            if isinstance(telemetry_result.get("data"), dict)
            else None,
            "error": telemetry_result.get("error"),
        }
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
