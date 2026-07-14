# Curator_Agent API Contract

This document defines the baseline API contract for `Curator_Agent`.

`Curator_Agent` provides skill registry, skill lifecycle, skill search, controlled skill
execution, and performance policy curation output for other agents.

## Standard Headers

```http
Content-Type: application/json
X-Correlation-ID: <uuid>
X-API-KEY: <curator-agent-api-key>
```

## Standard Response Envelope

Operational contract endpoints return this envelope:

```json
{
  "status": "success",
  "agent_type": "curator-agent",
  "version": "0.1.0",
  "schema_version": "1.0",
  "timestamp": "2026-07-04T00:00:00Z",
  "correlation_id": null,
  "data": {},
  "metadata": {},
  "error": null,
  "confidence_score": null
}
```

## Operational Endpoints

```http
GET /health
GET /ready
GET /version
```

## Curator Endpoints

```http
POST /curate/performance-policy
POST /skills/register
GET /skills
GET /skills/search
POST /skills/{skill_id}/approve
POST /skills/{skill_id}/deprecate
POST /skills/{skill_id}/execute
GET /skills/{skill_id}
```

## Skill execution isolation contract

`POST /skills/{skill_id}/execute` uses `ContainerSandboxExecutor` by default. Runtime
defaults are:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `CURATOR_CONTAINER_SANDBOX_ENABLED` | `true` | Require isolated container execution |
| `CURATOR_CONTAINER_SANDBOX_FALLBACK` | `false` | Fail closed; never downgrade silently |
| `CURATOR_SANDBOX_IMAGE` | `curator-skill-sandbox:latest` | Required execution image |

The container is launched with no network, a read-only root filesystem, all capabilities
dropped, `no-new-privileges`, an unprivileged user, and resource limits. These constraints
are mandatory and must not be disabled by callers.

### Failure behavior

When container execution returns `container_unavailable` or `container_failed` and fallback
is disabled, Curator does not execute the skill and returns data shaped like:

```json
{
  "execution_status": "rejected_no_isolated_sandbox",
  "error": "isolated_container_sandbox_required",
  "container_execution_status": "container_unavailable",
  "container_error": "docker_binary_not_available",
  "fallback_used": false,
  "output": {}
}
```

This is an intentional breaking change. Production requires a working Docker facility and
the configured sandbox image; otherwise skill execution is rejected.

### Explicit fallback behavior

An operator may temporarily set `CURATOR_CONTAINER_SANDBOX_FALLBACK=true`. If container
startup then fails, Curator uses its restricted process runner and returns:

- `fallback_used=true`
- `security_alert="isolated_sandbox_fallback_used"`
- the latest `curator_container_sandbox_fallback_total` counter value
- `sandbox.mode="process_fallback"`
- `sandbox.isolation="best_effort_not_a_true_sandbox"`

Each fallback also emits a `CRITICAL` log with `SECURITY ALERT`, the skill id, container
failure, metric name, and metric value. Operations should alert on any occurrence.

The process runner uses AST screening, restricted builtins, a child process, and a timeout.
It blocks common object-introspection escape paths including `__class__`, `__bases__`,
`__subclasses__`, `__globals__`, and other dunder attributes before `exec()`. This is
defense in depth only: it is **best-effort isolation, not a true security sandbox**.

## Notes

1. Runtime readiness is reported through `/ready`.
2. Version and schema metadata are reported through `/version`.
3. Existing curator endpoints keep their current response models except for the documented
   fail-closed skill execution status.
4. Existing skill lifecycle and validation checks remain responsible for runtime behavior.
