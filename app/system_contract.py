from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

CURATOR_AGENT_TYPE = "curator-agent"
CURATOR_AGENT_VERSION = "0.1.0"
CURATOR_SERVICE_VERSION = "0.2.0"
SCHEMA_VERSION = "1.0"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract_response(
    *,
    status: str,
    data: Dict[str, Any] | None = None,
    metadata: Dict[str, Any] | None = None,
    error: Dict[str, Any] | None = None,
    confidence_score: float | None = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "agent_type": CURATOR_AGENT_TYPE,
        "version": CURATOR_AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "timestamp": utc_timestamp(),
        "correlation_id": None,
        "data": data,
        "metadata": metadata or {},
        "error": error,
        "confidence_score": confidence_score,
    }
