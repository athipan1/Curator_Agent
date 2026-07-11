import json
import subprocess
from types import SimpleNamespace

from app.container_sandbox import ContainerSandboxExecutor, OptionalContainerExecutor


class _Fallback:
    def execute(self, **kwargs):
        return {
            "skill_id": kwargs["skill_id"],
            "execution_status": "success",
            "output": {"signal": "hold", "confidence": 0.5},
        }


def _kwargs():
    return {
        "skill_id": "skill-1",
        "code": "def signal(price):\n    return {'signal': 'buy', 'confidence': 0.8}",
        "inputs": {"price": 120.0},
        "function_name": None,
        "timeout_seconds": 1.0,
    }


def test_container_command_enforces_security_limits(monkeypatch):
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: "/usr/bin/docker")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "skill_id": "skill-1",
                    "execution_status": "success",
                    "output": {"signal": "buy", "confidence": 0.8},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("app.container_sandbox.subprocess.run", fake_run)
    executor = ContainerSandboxExecutor(
        image="sandbox:test",
        memory_limit="64m",
        cpu_limit="0.25",
        pids_limit=32,
    )

    result = executor.execute(**_kwargs())

    command = captured["command"]
    assert result["execution_status"] == "success"
    assert result["sandbox"]["mode"] == "container"
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges:true" in command
    assert "--memory=64m" in command
    assert "--cpus=0.25" in command
    assert "--pids-limit=32" in command
    assert "--user=65534:65534" in command
    assert "sandbox:test" in command
    assert captured["kwargs"]["check"] is False


def test_container_timeout_is_fail_closed(monkeypatch):
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: "/usr/bin/docker")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1.0)

    monkeypatch.setattr("app.container_sandbox.subprocess.run", fake_run)
    result = ContainerSandboxExecutor().execute(**_kwargs())

    assert result["execution_status"] == "timeout"
    assert result["error"] == "skill_execution_timed_out"
    assert result["output"] == {}
    assert result["sandbox"]["network_access"] is False


def test_missing_docker_returns_container_unavailable(monkeypatch):
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: None)

    result = ContainerSandboxExecutor().execute(**_kwargs())

    assert result["execution_status"] == "container_unavailable"
    assert result["error"] == "docker_binary_not_available"


def test_disabled_container_uses_process_executor(monkeypatch):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "false")
    executor = OptionalContainerExecutor(
        container=ContainerSandboxExecutor(),
        fallback=_Fallback(),
    )

    result = executor.execute(**_kwargs())

    assert result["execution_status"] == "success"
    assert result["sandbox"]["mode"] == "process"
    assert result["sandbox"]["container_enabled"] is False


def test_enabled_container_falls_back_when_docker_missing(monkeypatch):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_FALLBACK", "true")
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: None)
    executor = OptionalContainerExecutor(
        container=ContainerSandboxExecutor(),
        fallback=_Fallback(),
    )

    result = executor.execute(**_kwargs())

    assert result["execution_status"] == "success"
    assert result["fallback_used"] is True
    assert result["sandbox"]["mode"] == "process_fallback"
    assert result["sandbox"]["container_error"] == "docker_binary_not_available"


def test_strict_container_mode_does_not_fallback(monkeypatch):
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("CURATOR_CONTAINER_SANDBOX_FALLBACK", "false")
    monkeypatch.setattr("app.container_sandbox.shutil.which", lambda binary: None)
    executor = OptionalContainerExecutor(
        container=ContainerSandboxExecutor(),
        fallback=_Fallback(),
    )

    result = executor.execute(**_kwargs())

    assert result["execution_status"] == "container_unavailable"
    assert result["fallback_used"] is False
