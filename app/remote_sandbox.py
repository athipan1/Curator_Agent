from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from app.worker_protocol import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    build_worker_signature,
    canonical_json_bytes,
    require_worker_api_key,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_production() -> bool:
    environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development"))
    return environment.strip().lower() in {"production", "prod"}


def _validated_worker_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        raise RuntimeError("CURATOR_SANDBOX_WORKER_URL must be configured.")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(
            "CURATOR_SANDBOX_WORKER_URL must use an absolute http or https URL."
        )
    if parsed.username or parsed.password:
        raise RuntimeError(
            "CURATOR_SANDBOX_WORKER_URL must not contain embedded credentials."
        )
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RuntimeError(
            "CURATOR_SANDBOX_WORKER_URL must contain only scheme, host and optional port."
        )
    if (
        _is_production()
        and parsed.scheme != "https"
        and not _env_bool("CURATOR_ALLOW_INSECURE_WORKER_HTTP", False)
    ):
        raise RuntimeError(
            "Production sandbox worker traffic requires HTTPS. Set "
            "CURATOR_ALLOW_INSECURE_WORKER_HTTP=true only for an explicitly isolated "
            "private network."
        )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", "")
    )


class RemoteSandboxExecutor:
    """Send approved skill execution to a dedicated signed sandbox worker."""

    def __init__(
        self,
        *,
        worker_url: str | None = None,
        api_key: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        self.worker_url = _validated_worker_url(
            worker_url
            if worker_url is not None
            else os.getenv("CURATOR_SANDBOX_WORKER_URL", "")
        )
        self.api_key = require_worker_api_key(api_key)
        self.request_timeout_seconds = float(
            request_timeout_seconds
            if request_timeout_seconds is not None
            else os.getenv("CURATOR_SANDBOX_WORKER_TIMEOUT_SECONDS", "10")
        )
        if not 1 <= self.request_timeout_seconds <= 60:
            raise RuntimeError(
                "CURATOR_SANDBOX_WORKER_TIMEOUT_SECONDS must be between 1 and 60."
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = canonical_json_bytes(payload) if payload is not None else b""
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        signature = build_worker_signature(
            self.api_key,
            method=method,
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        )
        headers = {
            HEADER_TIMESTAMP: timestamp,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: signature,
            "Accept": "application/json",
            "X-Correlation-ID": f"curator-worker-{nonce}",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = body
        request = urllib.request.Request(
            f"{self.worker_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        timeout = timeout_seconds or self.request_timeout_seconds
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")
                return response.status, json.loads(response_body) if response_body else {}
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(response_body) if response_body else {}
            except json.JSONDecodeError:
                parsed = {"error": {"code": "invalid_worker_error_response"}}
            return exc.code, parsed

    @staticmethod
    def _failure(
        *,
        skill_id: str,
        code: str,
        details: str | None = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "skill_id": skill_id,
            "execution_status": "rejected_remote_worker_unavailable",
            "error": code,
            "output": {},
            "fallback_used": False,
            "sandbox": {
                "mode": "remote_worker",
                "secure_execution_ready": False,
                "broker_access": False,
                "order_placement": False,
            },
        }
        if details:
            result["details"] = details[:500]
        return result

    def readiness(self) -> Dict[str, Any]:
        try:
            status_code, response = self._request("GET", "/ready")
        except Exception as exc:
            return {
                "ready": False,
                "mode": "remote_worker",
                "worker_endpoint_configured": True,
                "secure_execution_ready": False,
                "degraded": False,
                "fallback_enabled": False,
                "reason": "sandbox_worker_unreachable",
                "details": str(exc)[:500],
            }

        data = response.get("data") if isinstance(response, dict) else None
        data = data if isinstance(data, dict) else {}
        execution = data.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        ready = status_code == 200 and bool(data.get("ready")) and bool(
            execution.get("secure_execution_ready")
        )
        return {
            "ready": ready,
            "mode": "remote_worker",
            "worker_endpoint_configured": True,
            "worker_status_code": status_code,
            "secure_execution_ready": ready,
            "degraded": False,
            "fallback_enabled": False,
            "worker_execution": execution,
            "reason": None if ready else (
                data.get("reason")
                or (response.get("error") or {}).get("code")
                or "sandbox_worker_not_ready"
            ),
        }

    def execute(
        self,
        *,
        skill_id: str,
        code: str,
        inputs: Dict[str, Any],
        function_name: str | None = None,
        timeout_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        payload = {
            "skill_id": skill_id,
            "code": code,
            "inputs": inputs,
            "function_name": function_name,
            "timeout_seconds": float(timeout_seconds),
        }
        try:
            status_code, response = self._request(
                "POST",
                "/v1/execute",
                payload,
                timeout_seconds=max(
                    self.request_timeout_seconds,
                    min(float(timeout_seconds), 5.0) + 5.0,
                ),
            )
        except Exception as exc:
            return self._failure(
                skill_id=skill_id,
                code="sandbox_worker_unreachable",
                details=str(exc),
            )

        if status_code != 200 or response.get("status") != "success":
            error = response.get("error") if isinstance(response, dict) else None
            error = error if isinstance(error, dict) else {}
            return self._failure(
                skill_id=skill_id,
                code=str(error.get("code") or f"sandbox_worker_http_{status_code}"),
                details=str(error.get("message") or "remote sandbox worker rejected execution"),
            )

        result = response.get("data")
        if not isinstance(result, dict):
            return self._failure(
                skill_id=skill_id,
                code="invalid_sandbox_worker_response",
            )
        result["execution_backend"] = "remote_worker"
        result["fallback_used"] = False
        return result
