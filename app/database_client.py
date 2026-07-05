from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class DatabaseAgentClient:
    """Small stdlib HTTP client for optional Database_Agent telemetry.

    Curator must remain signal-only. This client only records execution telemetry,
    reads advisory skill rankings, and reads backtest status. Failures are
    non-fatal so Curator can keep serving approved skills when Database_Agent is
    unavailable, except for explicit backtest-gated approval paths.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("DATABASE_AGENT_URL") or "").rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("DATABASE_AGENT_API_KEY")
        self.timeout_seconds = float(timeout_seconds or os.getenv("DATABASE_AGENT_TIMEOUT_SECONDS", "1.0"))

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "skipped", "reason": "database_agent_url_not_configured"}

        body = None if payload is None else json.dumps(payload, default=str).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        if self.api_key:
            request.add_header("X-API-KEY", self.api_key)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {"status": "success", "data": None}
        except urllib.error.HTTPError as exc:
            return {
                "status": "failed",
                "error": f"database_agent_http_{exc.code}",
                "details": exc.read().decode("utf-8", errors="replace"),
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def create_skill_execution_log(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/skills/execution-logs", payload)

    def rank_skills(
        self,
        *,
        account_id: str | int = 1,
        symbol: Optional[str] = None,
        strategy_bucket: Optional[str] = None,
        market_regime: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        query = [f"account_id={urllib.parse.quote(str(account_id))}", f"limit={limit}"]
        if symbol:
            query.append(f"symbol={urllib.parse.quote(symbol.upper())}")
        if strategy_bucket:
            query.append(f"strategy_bucket={urllib.parse.quote(strategy_bucket)}")
        if market_regime:
            query.append(f"market_regime={urllib.parse.quote(market_regime)}")
        return self._request("GET", f"/skills/performance/rank?{'&'.join(query)}")

    def get_skill_backtest_status(self, skill_id: str) -> Dict[str, Any]:
        safe_skill_id = urllib.parse.quote(skill_id, safe="")
        return self._request("GET", f"/skills/{safe_skill_id}/backtest-status")
