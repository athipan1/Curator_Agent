from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


AuthRole = Literal["read", "execute", "admin"]

_OPEN_PATHS = {
    "/health",
    "/ready",
    "/version",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}
_ADMIN_SUFFIXES = (
    "/approve",
    "/approve-from-backtest",
    "/deprecate",
    "/versions",
    "/promote",
    "/rollback",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_production() -> bool:
    environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development"))
    return environment.strip().lower() in {"production", "prod"}


@dataclass(frozen=True)
class CuratorAuthConfig:
    read_api_key: str
    execute_api_key: str
    admin_api_key: str
    required: bool
    production: bool

    @classmethod
    def from_env(cls) -> "CuratorAuthConfig":
        production = _is_production()
        shared_key = os.getenv("CURATOR_API_KEY", "").strip()
        admin_key = os.getenv("CURATOR_ADMIN_API_KEY", "").strip() or shared_key
        execute_key = (
            os.getenv("CURATOR_EXECUTE_API_KEY", "").strip()
            or shared_key
            or admin_key
        )
        read_key = (
            os.getenv("CURATOR_READ_API_KEY", "").strip()
            or shared_key
            or execute_key
            or admin_key
        )
        any_key_configured = any((read_key, execute_key, admin_key))
        required = production or any_key_configured or _env_bool(
            "CURATOR_REQUIRE_API_KEY",
            False,
        )

        if required:
            missing_roles = [
                role
                for role, value in (
                    ("read", read_key),
                    ("execute", execute_key),
                    ("admin", admin_key),
                )
                if not value
            ]
            if missing_roles:
                roles = ",".join(missing_roles)
                raise RuntimeError(
                    "Curator API authentication is required but API keys are missing "
                    f"for roles: {roles}. Configure CURATOR_API_KEY or role-specific keys."
                )

        return cls(
            read_api_key=read_key,
            execute_api_key=execute_key,
            admin_api_key=admin_key,
            required=required,
            production=production,
        )


def _required_role(method: str, path: str) -> AuthRole | None:
    if path in _OPEN_PATHS:
        return None

    if method == "POST" and path in {"/skills/register", "/curate/performance-policy"}:
        return "admin"
    if method == "POST" and path.startswith("/skills/") and path.endswith(_ADMIN_SUFFIXES):
        return "admin"
    if method == "POST" and (
        path == "/skills/shadow-ensemble" or path.endswith("/execute")
    ):
        return "execute"
    return "read"


def _constant_time_match(supplied: str, expected: str) -> bool:
    if not supplied or not expected:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _authorized(supplied: str, role: AuthRole, config: CuratorAuthConfig) -> bool:
    accepted = [config.admin_api_key]
    if role in {"read", "execute"}:
        accepted.append(config.execute_api_key)
    if role == "read":
        accepted.append(config.read_api_key)
    return any(_constant_time_match(supplied, expected) for expected in dict.fromkeys(accepted))


def _auth_error(request: Request, *, status_code: int, code: str, role: AuthRole) -> JSONResponse:
    correlation_id = request.headers.get("X-Correlation-ID")
    message = "API key is required." if status_code == 401 else "API key is not authorized."
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "agent_type": "curator-agent",
            "version": "0.1.0",
            "schema_version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "data": None,
            "metadata": {"required_role": role},
            "error": {"code": code, "message": message},
            "confidence_score": None,
        },
    )


def attach_api_key_auth(
    app: FastAPI,
    config: CuratorAuthConfig | None = None,
) -> CuratorAuthConfig:
    """Protect Curator control-plane and execution endpoints with role API keys.

    Operational endpoints remain open. Development remains backward-compatible
    when no key is configured. Production always fails startup unless effective
    read, execute, and admin credentials are available.
    """

    auth_config = config or CuratorAuthConfig.from_env()
    app.state.curator_auth = auth_config

    @app.middleware("http")
    async def curator_api_key_middleware(request: Request, call_next):
        role = _required_role(request.method.upper(), request.url.path)
        if role is None or not auth_config.required:
            return await call_next(request)

        supplied = request.headers.get("X-API-KEY", "")
        if not supplied:
            return _auth_error(
                request,
                status_code=401,
                code="missing_api_key",
                role=role,
            )
        if not _authorized(supplied, role, auth_config):
            return _auth_error(
                request,
                status_code=403,
                code="insufficient_api_key_role",
                role=role,
            )
        request.state.curator_auth_role = role
        return await call_next(request)

    return auth_config
