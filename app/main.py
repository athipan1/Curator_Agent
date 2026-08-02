from __future__ import annotations

import os
from typing import Any

from app.main_legacy import *  # noqa: F401,F403
import app.main_legacy as _legacy

from app.container_sandbox import ContainerSandboxExecutor, OptionalContainerExecutor
from app.database_client import DatabaseAgentClient
from app.executor import SafeSkillExecutor
from app.performance_aware_executor import PerformanceAwareExecutor
from app.readiness import attach_runtime_readiness
from app.registry import SkillRegistry
from app.remote_sandbox import RemoteSandboxExecutor
from app.schema_enforcing_executor import SchemaEnforcingExecutor
from app.shadow_ensemble import attach_shadow_ensemble_routes
from app.version_api import attach_version_lifecycle_routes


DEFAULT_DB_PATH = _legacy.DEFAULT_DB_PATH


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _seeded_backtest_skill_id(registry: SkillRegistry) -> str | None:
    if not _env_bool("CURATOR_SEED_BACKTEST_SKILL", True):
        return None
    skill_id = os.getenv(
        "CURATOR_SEED_BACKTEST_SKILL_ID",
        "hourly-sma-crossover",
    )
    try:
        registry.get(skill_id)
    except KeyError:
        return None
    return skill_id


def _build_isolated_executor(base_executor: SafeSkillExecutor) -> Any:
    worker_url = os.getenv("CURATOR_SANDBOX_WORKER_URL", "").strip()
    worker_required = _env_bool("CURATOR_REQUIRE_SANDBOX_WORKER", False)
    if worker_url:
        return RemoteSandboxExecutor(worker_url=worker_url)
    if worker_required:
        raise RuntimeError(
            "CURATOR_REQUIRE_SANDBOX_WORKER=true but CURATOR_SANDBOX_WORKER_URL is missing."
        )
    return OptionalContainerExecutor(
        container=ContainerSandboxExecutor(),
        fallback=base_executor,
    )


def create_app(
    registry: SkillRegistry | None = None,
    executor: SafeSkillExecutor | None = None,
    database_client: DatabaseAgentClient | None = None,
):
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    skill_database_client = database_client or DatabaseAgentClient()
    base_executor = executor or SafeSkillExecutor()
    isolated_executor = _build_isolated_executor(base_executor)
    schema_executor = SchemaEnforcingExecutor(
        registry=skill_registry,
        delegate=isolated_executor,
    )
    performance_executor = PerformanceAwareExecutor(
        delegate=schema_executor,
        database_client=skill_database_client,
    )
    app = _legacy.create_app(
        registry=skill_registry,
        executor=performance_executor,
        database_client=skill_database_client,
    )
    attach_version_lifecycle_routes(app, skill_registry)
    attach_shadow_ensemble_routes(app, skill_registry, performance_executor)
    attach_runtime_readiness(
        app,
        executor=performance_executor,
        database_client=skill_database_client,
        seeded_backtest_skill_id=_seeded_backtest_skill_id(skill_registry),
    )
    worker_enabled = isinstance(isolated_executor, RemoteSandboxExecutor)
    app.state.skill_schema_contracts_enabled = True
    app.state.confidence_calibration_enabled = True
    app.state.performance_decay_advisory_enabled = True
    app.state.champion_challenger_shadow_enabled = True
    app.state.sandbox_worker_enabled = worker_enabled
    app.state.sandbox_worker_url = (
        isolated_executor.worker_url if worker_enabled else None
    )
    app.state.container_sandbox_enabled = (
        True if worker_enabled else isolated_executor.enabled
    )
    app.state.container_sandbox_fallback_enabled = (
        False if worker_enabled else isolated_executor.allow_fallback
    )
    return app


app = create_app()
