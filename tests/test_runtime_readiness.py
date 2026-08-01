from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.container_sandbox import ContainerSandboxExecutor
from app.main import create_app
from app.registry import SkillRegistry


def test_ready_reports_explicit_process_mode_as_degraded(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_FALLBACK", "false")
    monkeypatch.delenv("CURATOR_REQUIRE_DATABASE_TELEMETRY", raising=False)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))

    response = TestClient(create_app(registry)).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["ready"] is True
    assert payload["data"]["execution"]["mode"] == "process"
    assert payload["data"]["execution"]["degraded"] is True
    assert payload["data"]["execution"]["secure_execution_ready"] is False


def test_ready_fails_closed_when_required_container_runtime_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_FALLBACK", "false")
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: None)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))

    response = TestClient(create_app(registry)).get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["data"]["ready"] is False
    assert payload["data"]["execution"]["reason"] == "docker_binary_not_available"
    assert "docker_binary_not_available" in payload["data"]["blockers"]
    assert payload["error"]["code"] == "runtime_not_ready"


def test_ready_requires_database_configuration_in_strict_telemetry_mode(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "false")
    monkeypatch.setenv("CURATOR_REQUIRE_DATABASE_TELEMETRY", "true")
    monkeypatch.delenv("DATABASE_AGENT_URL", raising=False)
    registry = SkillRegistry(str(tmp_path / "skills.sqlite3"))

    response = TestClient(create_app(registry)).get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["data"]["database_telemetry_required"] is True
    assert payload["data"]["database_telemetry_ready"] is False
    assert "required_database_telemetry_not_configured" in payload["data"]["blockers"]


def test_container_probe_checks_daemon_and_image(monkeypatch):
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: "/usr/bin/docker")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "info":
            return SimpleNamespace(returncode=0, stdout="27.5.1\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="sha256:abc123\n", stderr="")

    monkeypatch.setattr("app.container_sandbox.subprocess.run", fake_run)

    result = ContainerSandboxExecutor(image="sandbox:test").readiness()

    assert result["ready"] is True
    assert result["secure_execution_ready"] is True
    assert result["docker_server_version"] == "27.5.1"
    assert result["image_id"] == "sha256:abc123"
    assert commands[0][:2] == ["docker", "info"]
    assert commands[1][:4] == ["docker", "image", "inspect", "sandbox:test"]


def test_container_probe_distinguishes_missing_image(monkeypatch):
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: "/usr/bin/docker")

    def fake_run(command, **kwargs):
        if command[1] == "info":
            return SimpleNamespace(returncode=0, stdout="27.5.1\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="No such image")

    monkeypatch.setattr("app.container_sandbox.subprocess.run", fake_run)

    result = ContainerSandboxExecutor(image="missing:test").readiness()

    assert result["ready"] is False
    assert result["reason"] == "sandbox_image_unavailable"
    assert result["secure_execution_ready"] is False
