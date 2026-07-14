from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


class SandboxFallbackCounter:
    """Thread-safe process-local counter for explicit isolation downgrades."""

    metric_name = "curator_container_sandbox_fallback_total"

    def __init__(self) -> None:
        self._total = 0
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            self._total += 1
            return self._total

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


DEFAULT_SANDBOX_FALLBACK_COUNTER = SandboxFallbackCounter()


class ContainerSandboxExecutor:
    """Run skill code in an ephemeral, resource-limited Docker container."""

    def __init__(
        self,
        *,
        image: str | None = None,
        docker_binary: str | None = None,
        memory_limit: str | None = None,
        cpu_limit: str | None = None,
        pids_limit: int | None = None,
    ) -> None:
        self.image = image or os.getenv("CURATOR_SANDBOX_IMAGE", "curator-skill-sandbox:latest")
        self.docker_binary = docker_binary or os.getenv("CURATOR_DOCKER_BINARY", "docker")
        self.memory_limit = memory_limit or os.getenv("CURATOR_SANDBOX_MEMORY", "64m")
        self.cpu_limit = cpu_limit or os.getenv("CURATOR_SANDBOX_CPUS", "0.25")
        self.pids_limit = int(pids_limit or os.getenv("CURATOR_SANDBOX_PIDS_LIMIT", "32"))

    @property
    def available(self) -> bool:
        return shutil.which(self.docker_binary) is not None

    def execute(
        self,
        *,
        skill_id: str,
        code: str,
        inputs: Dict[str, Any],
        function_name: str | None = None,
        timeout_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        if not self.available:
            return {
                "skill_id": skill_id,
                "execution_status": "container_unavailable",
                "error": "docker_binary_not_available",
                "output": {},
                "sandbox": self._sandbox_metadata(),
            }

        payload = {
            "skill_id": skill_id,
            "code": code,
            "inputs": inputs,
            "function_name": function_name,
        }
        with tempfile.TemporaryDirectory(prefix="curator-sandbox-") as tmp_dir:
            input_path = Path(tmp_dir) / "input.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            command = [
                self.docker_binary,
                "run",
                "--rm",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                f"--memory={self.memory_limit}",
                f"--cpus={self.cpu_limit}",
                f"--pids-limit={self.pids_limit}",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
                "--user=65534:65534",
                f"--mount=type=bind,src={input_path},dst=/input.json,readonly",
                self.image,
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=max(0.1, timeout_seconds) + 1.0,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "skill_id": skill_id,
                    "execution_status": "timeout",
                    "error": "skill_execution_timed_out",
                    "output": {},
                    "sandbox": self._sandbox_metadata(),
                }
            except Exception as exc:
                return {
                    "skill_id": skill_id,
                    "execution_status": "container_failed",
                    "error": str(exc),
                    "output": {},
                    "sandbox": self._sandbox_metadata(),
                }

        stdout = completed.stdout.strip()
        if completed.returncode != 0:
            return {
                "skill_id": skill_id,
                "execution_status": "container_failed",
                "error": "container_process_failed",
                "details": completed.stderr.strip()[-1000:],
                "output": {},
                "sandbox": self._sandbox_metadata(),
            }
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "skill_id": skill_id,
                "execution_status": "container_failed",
                "error": "invalid_container_response",
                "details": stdout[-1000:],
                "output": {},
                "sandbox": self._sandbox_metadata(),
            }
        if not isinstance(result, dict):
            return {
                "skill_id": skill_id,
                "execution_status": "container_failed",
                "error": "container_response_must_be_object",
                "output": {},
                "sandbox": self._sandbox_metadata(),
            }
        result.setdefault("skill_id", skill_id)
        result["sandbox"] = self._sandbox_metadata()
        return result

    def _sandbox_metadata(self) -> Dict[str, Any]:
        return {
            "mode": "container",
            "image": self.image,
            "network_access": False,
            "read_only_filesystem": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "pids_limit": self.pids_limit,
            "broker_access": False,
            "order_placement": False,
        }


class OptionalContainerExecutor:
    """Prefer container isolation and fail closed unless fallback is explicitly enabled."""

    def __init__(
        self,
        *,
        container: ContainerSandboxExecutor,
        fallback: Any,
        fallback_counter: SandboxFallbackCounter | None = None,
    ) -> None:
        self.container = container
        self.fallback = fallback
        self.fallback_counter = fallback_counter or DEFAULT_SANDBOX_FALLBACK_COUNTER
        self.enabled = os.getenv("CURATOR_CONTAINER_SANDBOX_ENABLED", "true").lower() == "true"
        self.allow_fallback = (
            os.getenv("CURATOR_CONTAINER_SANDBOX_FALLBACK", "false").lower() == "true"
        )

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        if not self.enabled:
            result = self.fallback.execute(**kwargs)
            result.setdefault(
                "sandbox",
                {
                    "mode": "process",
                    "container_enabled": False,
                    "isolation": "best_effort_not_a_true_sandbox",
                },
            )
            return result

        container_result = self.container.execute(**kwargs)
        container_status = container_result.get("execution_status")
        if container_status not in {"container_unavailable", "container_failed"}:
            return container_result

        if not self.allow_fallback:
            return {
                **container_result,
                "execution_status": "rejected_no_isolated_sandbox",
                "error": "isolated_container_sandbox_required",
                "container_execution_status": container_status,
                "container_error": container_result.get("error"),
                "fallback_used": False,
                "output": {},
            }

        fallback_total = self.fallback_counter.increment()
        logger.critical(
            "SECURITY ALERT: isolated container execution failed; using explicitly enabled "
            "best-effort process fallback; metric_name=%s metric_value=%d skill_id=%s "
            "container_execution_status=%s container_error=%s",
            self.fallback_counter.metric_name,
            fallback_total,
            kwargs.get("skill_id"),
            container_status,
            container_result.get("error"),
        )
        fallback_result = self.fallback.execute(**kwargs)
        fallback_result["sandbox"] = {
            "mode": "process_fallback",
            "container_enabled": True,
            "container_execution_status": container_status,
            "container_error": container_result.get("error"),
            "isolation": "best_effort_not_a_true_sandbox",
            "broker_access": False,
            "order_placement": False,
        }
        fallback_result["fallback_used"] = True
        fallback_result["security_alert"] = "isolated_sandbox_fallback_used"
        fallback_result[self.fallback_counter.metric_name] = fallback_total
        return fallback_result
