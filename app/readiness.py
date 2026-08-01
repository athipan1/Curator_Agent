from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.system_contract import contract_response


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def executor_readiness(executor: Any) -> Dict[str, Any]:
    """Walk executor decorators until a concrete readiness probe is found."""
    current = executor
    visited: set[int] = set()
    for _ in range(12):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        readiness = getattr(current, "readiness", None)
        if callable(readiness):
            result = readiness()
            if isinstance(result, dict):
                return result
        current = getattr(current, "delegate", None)

    return {
        "ready": True,
        "mode": "process",
        "secure_execution_ready": False,
        "degraded": True,
        "reason": "executor_has_no_runtime_probe",
        "isolation": "best_effort_not_a_true_sandbox",
    }


def attach_runtime_readiness(
    app: FastAPI,
    *,
    executor: Any,
    database_client: Any,
) -> FastAPI:
    """Replace the legacy optimistic readiness route with dependency-aware readiness."""

    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != "/ready"
    ]

    @app.get("/ready")
    async def runtime_ready() -> JSONResponse:
        execution = executor_readiness(executor)
        require_database_telemetry = _bool_env(
            "CURATOR_REQUIRE_DATABASE_TELEMETRY",
            False,
        )
        database_ready = bool(database_client.enabled) or not require_database_telemetry
        ready = bool(execution.get("ready")) and database_ready

        blockers: list[str] = []
        if not execution.get("ready"):
            blockers.append(str(execution.get("reason") or "execution_runtime_unavailable"))
        if not database_ready:
            blockers.append("required_database_telemetry_not_configured")

        payload = contract_response(
            status="success" if ready else "unavailable",
            data={
                "ready": ready,
                "storage": "sqlite",
                "execution_enabled": True,
                "execution": execution,
                "database_telemetry_enabled": bool(database_client.enabled),
                "database_telemetry_required": require_database_telemetry,
                "database_telemetry_ready": database_ready,
                "blockers": blockers,
            },
            metadata={
                "contract_source": "curator-agent-runtime-readiness",
                "liveness_endpoint": "/health",
            },
            error=(
                None
                if ready
                else {
                    "code": "runtime_not_ready",
                    "message": "One or more required Curator runtime dependencies are unavailable.",
                }
            ),
            confidence_score=1.0,
        )
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    app.state.runtime_readiness_enabled = True
    return app
