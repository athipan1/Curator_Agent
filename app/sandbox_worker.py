from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.container_sandbox import ContainerSandboxExecutor
from app.worker_protocol import (
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    NONCE_PATTERN,
    SandboxWorkerLimits,
    WorkerExecutionRequest,
    require_worker_api_key,
    verify_worker_signature,
)


class NonceReplayGuard:
    """Bounded in-memory replay protection for signed worker requests."""

    def __init__(self, *, ttl_seconds: int, max_entries: int = 10_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def consume(self, nonce: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._lock:
            expired = [
                value
                for value, expires_at in self._entries.items()
                if expires_at <= current
            ]
            for value in expired:
                self._entries.pop(value, None)

            if nonce in self._entries:
                return False
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
            self._entries[nonce] = current + self.ttl_seconds
            return True


def _worker_response(
    *,
    status_code: int,
    status: str,
    data: Any = None,
    error_code: str | None = None,
    message: str | None = None,
    correlation_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "service": "curator-sandbox-worker",
            "version": "1.0",
            "correlation_id": correlation_id,
            "data": data,
            "error": (
                None
                if error_code is None
                else {
                    "code": error_code,
                    "message": message or error_code,
                }
            ),
        },
    )


def create_worker_app(
    *,
    executor: ContainerSandboxExecutor | None = None,
    api_key: str | None = None,
    limits: SandboxWorkerLimits | None = None,
    replay_guard: NonceReplayGuard | None = None,
) -> FastAPI:
    worker_key = require_worker_api_key(api_key)
    worker_limits = limits or SandboxWorkerLimits.from_env()
    sandbox_executor = executor or ContainerSandboxExecutor()
    nonces = replay_guard or NonceReplayGuard(
        ttl_seconds=worker_limits.max_clock_skew_seconds * 2,
    )
    concurrency = threading.BoundedSemaphore(worker_limits.max_concurrency)

    app = FastAPI(
        title="Curator Sandbox Worker",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def authenticate_worker_request(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        correlation_id = request.headers.get("X-Correlation-ID")
        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > worker_limits.max_body_bytes:
                    return _worker_response(
                        status_code=413,
                        status="error",
                        error_code="worker_request_too_large",
                        message="Sandbox worker request exceeds the configured size limit.",
                        correlation_id=correlation_id,
                    )
            except ValueError:
                return _worker_response(
                    status_code=400,
                    status="error",
                    error_code="invalid_content_length",
                    correlation_id=correlation_id,
                )

        body = await request.body()
        if len(body) > worker_limits.max_body_bytes:
            return _worker_response(
                status_code=413,
                status="error",
                error_code="worker_request_too_large",
                message="Sandbox worker request exceeds the configured size limit.",
                correlation_id=correlation_id,
            )
        request._body = body  # Preserve the authenticated bytes for FastAPI body parsing.

        timestamp = request.headers.get(HEADER_TIMESTAMP, "")
        nonce = request.headers.get(HEADER_NONCE, "")
        signature = request.headers.get(HEADER_SIGNATURE, "")
        if not timestamp or not nonce or not signature:
            return _worker_response(
                status_code=401,
                status="error",
                error_code="missing_worker_signature",
                correlation_id=correlation_id,
            )
        if not NONCE_PATTERN.fullmatch(nonce):
            return _worker_response(
                status_code=401,
                status="error",
                error_code="invalid_worker_nonce",
                correlation_id=correlation_id,
            )
        try:
            request_time = int(timestamp)
        except ValueError:
            return _worker_response(
                status_code=401,
                status="error",
                error_code="invalid_worker_timestamp",
                correlation_id=correlation_id,
            )
        if abs(int(time.time()) - request_time) > worker_limits.max_clock_skew_seconds:
            return _worker_response(
                status_code=401,
                status="error",
                error_code="stale_worker_request",
                correlation_id=correlation_id,
            )
        if not verify_worker_signature(
            worker_key,
            signature,
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        ):
            return _worker_response(
                status_code=403,
                status="error",
                error_code="invalid_worker_signature",
                correlation_id=correlation_id,
            )
        if not nonces.consume(nonce):
            return _worker_response(
                status_code=409,
                status="error",
                error_code="replayed_worker_request",
                correlation_id=correlation_id,
            )
        return await call_next(request)

    @app.get("/health")
    async def health() -> JSONResponse:
        return _worker_response(
            status_code=200,
            status="success",
            data={
                "status": "healthy",
                "execution_surface": "sandbox_only",
                "broker_access": False,
                "order_placement": False,
            },
        )

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        execution = await run_in_threadpool(sandbox_executor.readiness)
        ready_state = bool(execution.get("ready")) and bool(
            execution.get("secure_execution_ready")
        )
        return _worker_response(
            status_code=200 if ready_state else 503,
            status="success" if ready_state else "unavailable",
            data={
                "ready": ready_state,
                "execution": execution,
                "max_concurrency": worker_limits.max_concurrency,
                "max_body_bytes": worker_limits.max_body_bytes,
                "fallback_enabled": False,
            },
            error_code=None if ready_state else "sandbox_runtime_not_ready",
            correlation_id=request.headers.get("X-Correlation-ID"),
        )

    @app.post("/v1/execute")
    async def execute(
        payload: WorkerExecutionRequest,
        request: Request,
    ) -> JSONResponse:
        acquired = await run_in_threadpool(
            concurrency.acquire,
            True,
            worker_limits.queue_timeout_seconds,
        )
        if not acquired:
            return _worker_response(
                status_code=429,
                status="error",
                error_code="sandbox_worker_busy",
                message="Sandbox worker concurrency limit is saturated.",
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
        try:
            result: Dict[str, Any] = await run_in_threadpool(
                sandbox_executor.execute,
                skill_id=payload.skill_id,
                code=payload.code,
                inputs=payload.inputs,
                function_name=payload.function_name,
                timeout_seconds=payload.timeout_seconds,
            )
        finally:
            concurrency.release()

        result["worker"] = {
            "service": "curator-sandbox-worker",
            "authenticated": True,
            "fallback_enabled": False,
        }
        return _worker_response(
            status_code=200,
            status="success",
            data=result,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )

    app.state.sandbox_executor = sandbox_executor
    app.state.worker_limits = worker_limits
    app.state.replay_guard = nonces
    app.state.fallback_enabled = False
    return app


app = create_worker_app()
