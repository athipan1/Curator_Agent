from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.container_sandbox import ContainerSandboxExecutor


def _execute_kwargs() -> dict:
    return {
        "skill_id": "shared-root-skill",
        "code": "def signal(): return {'signal': 'hold'}",
        "inputs": {},
        "function_name": "signal",
        "timeout_seconds": 1.0,
    }


def test_required_work_root_fails_closed_when_missing() -> None:
    executor = ContainerSandboxExecutor(require_work_root=True)

    readiness = executor.readiness()
    result = executor.execute(**_execute_kwargs())

    assert readiness["ready"] is False
    assert readiness["reason"] == "sandbox_work_root_not_configured"
    assert result["execution_status"] == "container_unavailable"
    assert result["error"] == "sandbox_work_root_not_configured"
    assert result["output"] == {}


def test_work_root_must_be_absolute() -> None:
    with pytest.raises(RuntimeError, match="absolute path"):
        ContainerSandboxExecutor(work_root="relative/work-root")


def test_missing_configured_work_root_is_not_ready(tmp_path: Path) -> None:
    executor = ContainerSandboxExecutor(
        work_root=tmp_path / "missing",
        require_work_root=True,
    )

    readiness = executor.readiness()

    assert readiness["ready"] is False
    assert readiness["reason"] == "sandbox_work_root_unavailable"
    assert readiness["shared_work_root_configured"] is True
    assert readiness["shared_work_root_required"] is True


def test_execution_creates_bind_source_under_shared_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "shared"
    work_root.mkdir()
    captured: dict = {}
    monkeypatch.setattr(
        "app.container_sandbox.shutil.which",
        lambda _binary: "/usr/bin/docker",
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "skill_id": "shared-root-skill",
                    "execution_status": "success",
                    "output": {"signal": "hold"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("app.container_sandbox.subprocess.run", fake_run)
    executor = ContainerSandboxExecutor(
        image="sandbox:test",
        work_root=work_root,
        require_work_root=True,
    )

    result = executor.execute(**_execute_kwargs())

    mount_argument = next(
        item for item in captured["command"] if item.startswith("--mount=type=bind")
    )
    source = mount_argument.split("src=", 1)[1].split(",dst=", 1)[0]
    assert Path(source).parent.parent == work_root
    assert result["execution_status"] == "success"
    assert result["sandbox"]["shared_work_root_configured"] is True
    assert result["sandbox"]["shared_work_root_required"] is True
