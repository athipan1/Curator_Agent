from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from app.remote_sandbox import RemoteSandboxExecutor
from app.worker_protocol import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    verify_worker_signature,
)


WORKER_KEY = "remote-worker-key-000000000000000000000000000"


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _lower_headers(request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def test_remote_readiness_signs_request_and_reports_secure_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "status": "success",
                "data": {
                    "ready": True,
                    "execution": {
                        "mode": "container",
                        "secure_execution_ready": True,
                    },
                },
                "error": None,
            }
        )

    monkeypatch.setattr("app.remote_sandbox.time.time", lambda: 1_720_000_000)
    monkeypatch.setattr(
        "app.remote_sandbox.secrets.token_urlsafe",
        lambda _size: "nonce_value_remote_0001",
    )
    monkeypatch.setattr("app.remote_sandbox.urllib.request.urlopen", fake_urlopen)
    executor = RemoteSandboxExecutor(
        worker_url="http://worker:8020",
        api_key=WORKER_KEY,
        request_timeout_seconds=5,
    )

    readiness = executor.readiness()

    assert readiness["ready"] is True
    assert readiness["mode"] == "remote_worker"
    assert readiness["secure_execution_ready"] is True
    request = captured["request"]
    headers = _lower_headers(request)
    timestamp = headers[HEADER_TIMESTAMP.lower()]
    nonce = headers[HEADER_NONCE.lower()]
    signature = headers[HEADER_SIGNATURE.lower()]
    assert request.full_url == "http://worker:8020/ready"
    assert verify_worker_signature(
        WORKER_KEY,
        signature,
        method="GET",
        path="/ready",
        timestamp=timestamp,
        nonce=nonce,
        body=b"",
    )
    assert not verify_worker_signature(
        WORKER_KEY,
        signature,
        method="POST",
        path="/v1/execute",
        timestamp=timestamp,
        nonce=nonce,
        body=b"",
    )


def test_remote_execution_returns_worker_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        assert request.full_url == "http://worker:8020/v1/execute"
        return FakeResponse(
            {
                "status": "success",
                "data": {
                    "skill_id": "skill-1",
                    "execution_status": "success",
                    "output": {"signal": "hold"},
                    "sandbox": {
                        "mode": "container",
                        "network_access": False,
                        "read_only_filesystem": True,
                    },
                },
                "error": None,
            }
        )

    monkeypatch.setattr("app.remote_sandbox.urllib.request.urlopen", fake_urlopen)
    executor = RemoteSandboxExecutor(
        worker_url="http://worker:8020",
        api_key=WORKER_KEY,
    )

    result = executor.execute(
        skill_id="skill-1",
        code="def signal(): return {'signal': 'hold'}",
        inputs={},
        function_name="signal",
        timeout_seconds=1.0,
    )

    assert result["execution_status"] == "success"
    assert result["execution_backend"] == "remote_worker"
    assert result["fallback_used"] is False
    assert result["sandbox"]["network_access"] is False


def test_remote_execution_fails_closed_when_worker_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("worker unavailable")

    monkeypatch.setattr("app.remote_sandbox.urllib.request.urlopen", unavailable)
    executor = RemoteSandboxExecutor(
        worker_url="http://worker:8020",
        api_key=WORKER_KEY,
    )

    result = executor.execute(
        skill_id="skill-1",
        code="def signal(): return {'signal': 'hold'}",
        inputs={},
    )

    assert result["execution_status"] == "rejected_remote_worker_unavailable"
    assert result["fallback_used"] is False
    assert result["output"] == {}
    assert result["sandbox"]["mode"] == "remote_worker"
