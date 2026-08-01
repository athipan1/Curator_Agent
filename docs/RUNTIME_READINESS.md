# Curator runtime readiness

`GET /health` is a liveness endpoint. It confirms that the API process is running and remains suitable for container health checks.

`GET /ready` is a dependency-aware readiness endpoint. It returns HTTP `200` only when every required runtime dependency is available. It returns HTTP `503` with `status="unavailable"` and explicit blockers when Curator cannot safely serve its configured execution contract.

## Container sandbox checks

When `CURATOR_CONTAINER_SANDBOX_ENABLED=true`, readiness verifies:

1. the configured Docker CLI exists;
2. the Docker daemon is reachable;
3. the image in `CURATOR_SANDBOX_IMAGE` is present locally.

The default image name remains:

```text
curator-skill-sandbox:latest
```

The image publishing workflow also publishes:

```text
painaidee/curator-skill-sandbox:latest
painaidee/curator-skill-sandbox:<git-sha>
```

Production should deploy an immutable SHA tag and set `CURATOR_SANDBOX_IMAGE` to that exact tag after pulling it onto the controlled execution host.

If the Docker runtime or image is missing and fallback is disabled, `/ready` returns `503` and execution remains fail-closed.

## Explicit process mode

When `CURATOR_CONTAINER_SANDBOX_ENABLED=false`, `/ready` may return `200`, but the response reports:

```json
{
  "mode": "process",
  "secure_execution_ready": false,
  "degraded": true,
  "isolation": "best_effort_not_a_true_sandbox"
}
```

This mode is suitable only for trusted development inputs. It is not an isolation boundary for hostile Python code.

When `CURATOR_CONTAINER_SANDBOX_FALLBACK=true`, readiness reports `mode="process_fallback"` and `degraded=true` if Docker is unavailable. Every actual fallback execution continues to emit the existing CRITICAL security alert and fallback counter.

## Database telemetry

When `CURATOR_REQUIRE_DATABASE_TELEMETRY=true`, readiness also requires `DATABASE_AGENT_URL` to be configured. Missing required telemetry configuration produces the blocker:

```text
required_database_telemetry_not_configured
```

## CI proof

The Curator CI workflow builds the real sandbox image, starts Curator directly on the GitHub runner Docker host, verifies `/ready`, registers and approves an authenticated skill, and executes it inside the hardened ephemeral container. This complements unit tests that mock failure branches.
