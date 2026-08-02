from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as curator_main
from app.database_client import DatabaseAgentClient
from app.executor import SafeSkillExecutor
from app.registry import SkillRegistry
from app.remote_sandbox import RemoteSandboxExecutor


WORKER_KEY = "main-worker-key-00000000000000000000000000000"


def test_build_executor_prefers_remote_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURATOR_SANDBOX_WORKER_URL", "http://worker:8020")
    monkeypatch.setenv("CURATOR_SANDBOX_WORKER_API_KEY", WORKER_KEY)

    executor = curator_main._build_isolated_executor(SafeSkillExecutor())

    assert isinstance(executor, RemoteSandboxExecutor)
    assert executor.worker_url == "http://worker:8020"


def test_required_worker_fails_startup_without_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURATOR_SANDBOX_WORKER_URL", raising=False)
    monkeypatch.setenv("CURATOR_REQUIRE_SANDBOX_WORKER", "true")

    with pytest.raises(RuntimeError, match="WORKER_URL is missing"):
        curator_main._build_isolated_executor(SafeSkillExecutor())


def test_runtime_readiness_reports_remote_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CURATOR_SANDBOX_WORKER_URL", "http://worker:8020")
    monkeypatch.setenv("CURATOR_SANDBOX_WORKER_API_KEY", WORKER_KEY)
    monkeypatch.setenv("CURATOR_REQUIRE_SANDBOX_WORKER", "true")
    monkeypatch.delenv("DATABASE_AGENT_URL", raising=False)
    monkeypatch.setattr(
        RemoteSandboxExecutor,
        "readiness",
        lambda self: {
            "ready": True,
            "mode": "remote_worker",
            "worker_url": self.worker_url,
            "secure_execution_ready": True,
            "degraded": False,
            "fallback_enabled": False,
        },
    )
    app = curator_main.create_app(
        registry=SkillRegistry(str(tmp_path / "worker-topology.sqlite3")),
        executor=SafeSkillExecutor(),
        database_client=DatabaseAgentClient(base_url=""),
    )

    response = TestClient(app).get("/ready")

    assert response.status_code == 200, response.text
    execution = response.json()["data"]["execution"]
    assert execution["mode"] == "remote_worker"
    assert execution["secure_execution_ready"] is True
    assert app.state.sandbox_worker_enabled is True
    assert app.state.container_sandbox_fallback_enabled is False
