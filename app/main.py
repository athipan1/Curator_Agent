from __future__ import annotations

from app.main_legacy import *  # noqa: F401,F403
import app.main_legacy as _legacy

from app.container_sandbox import ContainerSandboxExecutor, OptionalContainerExecutor
from app.database_client import DatabaseAgentClient
from app.executor import SafeSkillExecutor
from app.performance_aware_executor import PerformanceAwareExecutor
from app.registry import SkillRegistry
from app.schema_enforcing_executor import SchemaEnforcingExecutor
from app.shadow_ensemble import attach_shadow_ensemble_routes
from app.version_api import attach_version_lifecycle_routes


DEFAULT_DB_PATH = _legacy.DEFAULT_DB_PATH


def create_app(
    registry: SkillRegistry | None = None,
    executor: SafeSkillExecutor | None = None,
    database_client: DatabaseAgentClient | None = None,
):
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    skill_database_client = database_client or DatabaseAgentClient()
    base_executor = executor or SafeSkillExecutor()
    isolated_executor = OptionalContainerExecutor(
        container=ContainerSandboxExecutor(),
        fallback=base_executor,
    )
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
    app.state.skill_schema_contracts_enabled = True
    app.state.confidence_calibration_enabled = True
    app.state.performance_decay_advisory_enabled = True
    app.state.champion_challenger_shadow_enabled = True
    app.state.container_sandbox_enabled = isolated_executor.enabled
    app.state.container_sandbox_fallback_enabled = isolated_executor.allow_fallback
    return app


app = create_app()
