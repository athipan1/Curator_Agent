from __future__ import annotations

from app.main_legacy import *  # noqa: F401,F403
import app.main_legacy as _legacy

from app.database_client import DatabaseAgentClient
from app.executor import SafeSkillExecutor
from app.registry import SkillRegistry
from app.schema_enforcing_executor import SchemaEnforcingExecutor
from app.version_api import attach_version_lifecycle_routes


DEFAULT_DB_PATH = _legacy.DEFAULT_DB_PATH


def create_app(
    registry: SkillRegistry | None = None,
    executor: SafeSkillExecutor | None = None,
    database_client: DatabaseAgentClient | None = None,
):
    skill_registry = registry or SkillRegistry(DEFAULT_DB_PATH)
    schema_executor = SchemaEnforcingExecutor(
        registry=skill_registry,
        delegate=executor or SafeSkillExecutor(),
    )
    app = _legacy.create_app(
        registry=skill_registry,
        executor=schema_executor,
        database_client=database_client,
    )
    attach_version_lifecycle_routes(app, skill_registry)
    app.state.skill_schema_contracts_enabled = True
    return app


app = create_app()
