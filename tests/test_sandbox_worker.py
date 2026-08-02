from __future__ import annotations

import time
from typing import Any, Dict

from fastapi.testclient import TestClient

from app.sandbox_worker import NonceReplayGuard, create_worker_app
from app.worker_protocol import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SandboxWorkerLimits,
    build_worker_signature,
    canonical_json_bytes,
)


WORKER_KEY = "worker-test-key-0000000000000000000000000000"


class FakeContainerExecutor:
    def readiness(self) -> Dict[str, Any]:
        return {
            "ready": True,
            "mode": "container",
            "image": "curator-skill-sandbox:test",
            "secure_execution_ready": True,
            "network_access": False,
            "read_only_filesystem": True,
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return {
            "skill_id": kwargs["skill_id"],
            "execution_status": "success",
            "function_name": kwargs.get("function_name") or "signal",
            "output": {"signal": "hold", "confidence": 0.5},
            "sandbox": {
                "mode": "container",
                "network_access": False,
                "read_only_filesystem": True,
                "broker_access": False,
                "order_placement": False,
            },
        }


def _client() -> TestClient:
    app = create_worker_app(
        executor=FakeContainerExecutor(),
        api_key=WORKER_KEY,
        limits=SandboxWorkerLimits(
            max_body_bytes=1024,
            max_clock_skew_seconds=30,
            max_concurrency=2,
            queue_timeout_seconds=0.1,
        ),
        replay_guard=NonceReplayGuard(ttl_seconds=60),
    )
    return TestClient(app)


def _headers(
    body: bytes,
    *,
    nonce: str = "nonce_value_00000001",
    timestamp: int | None = None,
    signature: str | None = None,
) -> dict[str, str]:
    resolved_timestamp = str(int(time.time()) if timestamp is None else timestamp)
    resolved_signature = signature or build_worker_signature(
        WORKER_KEY,
        timestamp=resolved_timestamp,
        nonce=nonce,
        body=body,
    )
    return {
        HEADER_TIMESTAMP: resolved_timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: resolved_signature,
        "Content-Type": "application/json",
        "X-Correlation-ID": "worker-unit-test",
    }


def test_health_is_open_but_ready_requires_signature() -> None:
    client = _client()

    health = client.get("/health")
    ready_without_auth = client.get("/ready")
    ready = client.get("/ready", headers=_headers(b""))

    assert health.status_code == 200
    assert health.json()["data"]["broker_access"] is False
    assert ready_without_auth.status_code == 401
    assert ready.status_code == 200
    assert ready.json()["data"]["execution"]["secure_execution_ready"] is True


def test_worker_rejects_invalid_and_stale_signatures() -> None:
    client = _client()

    invalid = client.get(
        "/ready",
        headers=_headers(b"", signature="0" * 64),
    )
    stale = client.get(
        "/ready",
        headers=_headers(
            b"",
            nonce="nonce_value_00000002",
            timestamp=int(time.time()) - 120,
        ),
    )

    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "invalid_worker_signature"
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "stale_worker_request"


def test_worker_rejects_replayed_nonce() -> None:
    client = _client()
    headers = _headers(b"", nonce="nonce_value_00000003")

    first = client.get("/ready", headers=headers)
    replay = client.get("/ready", headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "replayed_worker_request"


def test_worker_executes_only_through_container_executor() -> None:
    client = _client()
    payload = {
        "skill_id": "skill-1",
        "code": "def signal(price): return {'signal': 'hold', 'price': price}",
        "inputs": {"price": 100},
        "function_name": "signal",
        "timeout_seconds": 1.0,
    }
    body = canonical_json_bytes(payload)

    response = client.post(
        "/v1/execute",
        content=body,
        headers=_headers(body, nonce="nonce_value_00000004"),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["execution_status"] == "success"
    assert data["sandbox"]["mode"] == "container"
    assert data["sandbox"]["network_access"] is False
    assert data["sandbox"]["read_only_filesystem"] is True
    assert data["worker"]["fallback_enabled"] is False


def test_worker_rejects_oversized_request_before_execution() -> None:
    client = _client()
    body = b"x" * 1025

    response = client.post(
        "/v1/execute",
        content=body,
        headers=_headers(body, nonce="nonce_value_00000005"),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "worker_request_too_large"
