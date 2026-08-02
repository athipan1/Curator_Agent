from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


HEADER_TIMESTAMP = "X-Curator-Worker-Timestamp"
HEADER_NONCE = "X-Curator-Worker-Nonce"
HEADER_SIGNATURE = "X-Curator-Worker-Signature"
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_worker_signature(
    secret: str,
    *,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    message = timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_worker_signature(
    secret: str,
    supplied_signature: str,
    *,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bool:
    if not secret or not supplied_signature:
        return False
    expected = build_worker_signature(
        secret,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    )
    return hmac.compare_digest(
        supplied_signature.encode("ascii", errors="ignore"),
        expected.encode("ascii"),
    )


def require_worker_api_key(value: str | None = None) -> str:
    key = (value if value is not None else os.getenv("CURATOR_SANDBOX_WORKER_API_KEY", "")).strip()
    if len(key) < 32:
        raise RuntimeError(
            "CURATOR_SANDBOX_WORKER_API_KEY must be configured with at least 32 characters."
        )
    return key


@dataclass(frozen=True)
class SandboxWorkerLimits:
    max_body_bytes: int
    max_clock_skew_seconds: int
    max_concurrency: int
    queue_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "SandboxWorkerLimits":
        max_body_bytes = int(os.getenv("CURATOR_SANDBOX_WORKER_MAX_BODY_BYTES", "65536"))
        max_clock_skew_seconds = int(
            os.getenv("CURATOR_SANDBOX_WORKER_MAX_CLOCK_SKEW_SECONDS", "30")
        )
        max_concurrency = int(
            os.getenv("CURATOR_SANDBOX_WORKER_MAX_CONCURRENCY", "2")
        )
        queue_timeout_seconds = float(
            os.getenv("CURATOR_SANDBOX_WORKER_QUEUE_TIMEOUT_SECONDS", "1")
        )
        if not 1024 <= max_body_bytes <= 1_048_576:
            raise RuntimeError(
                "CURATOR_SANDBOX_WORKER_MAX_BODY_BYTES must be between 1024 and 1048576."
            )
        if not 5 <= max_clock_skew_seconds <= 300:
            raise RuntimeError(
                "CURATOR_SANDBOX_WORKER_MAX_CLOCK_SKEW_SECONDS must be between 5 and 300."
            )
        if not 1 <= max_concurrency <= 32:
            raise RuntimeError(
                "CURATOR_SANDBOX_WORKER_MAX_CONCURRENCY must be between 1 and 32."
            )
        if not 0 <= queue_timeout_seconds <= 30:
            raise RuntimeError(
                "CURATOR_SANDBOX_WORKER_QUEUE_TIMEOUT_SECONDS must be between 0 and 30."
            )
        return cls(
            max_body_bytes=max_body_bytes,
            max_clock_skew_seconds=max_clock_skew_seconds,
            max_concurrency=max_concurrency,
            queue_timeout_seconds=queue_timeout_seconds,
        )


class WorkerExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    skill_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    code: str = Field(min_length=1, max_length=50_000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    function_name: str | None = Field(default=None, min_length=1, max_length=128)
    timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
