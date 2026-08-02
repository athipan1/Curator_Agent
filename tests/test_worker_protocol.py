from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.worker_protocol import (
    SandboxWorkerLimits,
    WorkerExecutionRequest,
    build_worker_signature,
    canonical_json_bytes,
    require_worker_api_key,
    verify_worker_signature,
)


def test_signature_round_trip_and_request_binding() -> None:
    secret = "w" * 48
    body = canonical_json_bytes({"skill_id": "skill-1", "inputs": {"price": 100}})
    signature = build_worker_signature(
        secret,
        method="POST",
        path="/v1/execute",
        timestamp="1720000000",
        nonce="nonce_value_0001",
        body=body,
    )

    assert verify_worker_signature(
        secret,
        signature,
        method="POST",
        path="/v1/execute",
        timestamp="1720000000",
        nonce="nonce_value_0001",
        body=body,
    )
    assert not verify_worker_signature(
        secret,
        signature,
        method="POST",
        path="/v1/execute",
        timestamp="1720000000",
        nonce="nonce_value_0001",
        body=body + b"x",
    )
    assert not verify_worker_signature(
        secret,
        signature,
        method="GET",
        path="/v1/execute",
        timestamp="1720000000",
        nonce="nonce_value_0001",
        body=body,
    )
    assert not verify_worker_signature(
        secret,
        signature,
        method="POST",
        path="/ready",
        timestamp="1720000000",
        nonce="nonce_value_0001",
        body=body,
    )


def test_canonical_json_is_stable() -> None:
    left = canonical_json_bytes({"b": 2, "a": {"z": 1, "y": 2}})
    right = canonical_json_bytes({"a": {"y": 2, "z": 1}, "b": 2})

    assert left == right


def test_worker_key_requires_minimum_entropy_length() -> None:
    with pytest.raises(RuntimeError, match="at least 32"):
        require_worker_api_key("short")

    assert require_worker_api_key("k" * 32) == "k" * 32


def test_worker_execution_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WorkerExecutionRequest.model_validate(
            {
                "skill_id": "skill-1",
                "code": "def signal(): return {'signal': 'hold'}",
                "inputs": {},
                "timeout_seconds": 1.0,
                "unexpected": True,
            }
        )


def test_worker_limits_reject_unsafe_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURATOR_SANDBOX_WORKER_MAX_CONCURRENCY", "0")

    with pytest.raises(RuntimeError, match="MAX_CONCURRENCY"):
        SandboxWorkerLimits.from_env()
