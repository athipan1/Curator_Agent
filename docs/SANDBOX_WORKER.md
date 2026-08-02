# Curator Sandbox Worker

The Sandbox Worker separates the Curator control-plane API from the Docker execution surface.

## Architecture

```text
Manager_Agent
    |
    | X-API-KEY
    v
Curator API container
    | signed HMAC request
    | no Docker CLI
    | no Docker socket
    v
Curator Sandbox Worker
    | Docker CLI / controlled daemon
    | host-visible shared work root
    v
Ephemeral skill sandbox container
    - network=none
    - read-only root filesystem
    - all capabilities dropped
    - no-new-privileges
    - memory, CPU and PID limits
    - no broker credentials
```

The API and worker share only `CURATOR_SANDBOX_WORKER_API_KEY`. Every worker request includes an HMAC-SHA256 signature, timestamp and one-time nonce. The signed message binds the HTTP method, request path, timestamp, nonce and exact request body, so a valid request cannot be moved to another endpoint or reused with another payload. The worker rejects missing, invalid, stale and replayed requests before parsing or executing skill code.

## Curator API configuration

Production with TLS:

```env
CURATOR_SANDBOX_WORKER_URL=https://curator-sandbox-worker.internal
CURATOR_SANDBOX_WORKER_API_KEY=<random value with at least 32 characters>
CURATOR_REQUIRE_SANDBOX_WORKER=true
CURATOR_CONTAINER_SANDBOX_FALLBACK=false
```

For an explicitly isolated private container network where TLS terminates outside the service, HTTP requires a deliberate override:

```env
CURATOR_SANDBOX_WORKER_URL=http://curator-sandbox-worker:8020
CURATOR_ALLOW_INSECURE_WORKER_HTTP=true
```

The worker URL accepts only an absolute HTTP or HTTPS origin containing scheme, host and optional port. Embedded credentials, paths, query strings, fragments and non-HTTP schemes are rejected. Public readiness and execution responses do not reveal the internal worker URL.

When `CURATOR_SANDBOX_WORKER_URL` is configured, the API uses `RemoteSandboxExecutor`. It does not attempt local Docker execution. If the worker is unavailable, execution fails closed with `rejected_remote_worker_unavailable`.

`CURATOR_REQUIRE_SANDBOX_WORKER=true` prevents accidental startup without the worker URL.

## Worker configuration

```env
CURATOR_SANDBOX_WORKER_API_KEY=<same worker key used by the API>
CURATOR_SANDBOX_IMAGE=painaidee/curator-skill-sandbox:<immutable-git-sha>
CURATOR_SANDBOX_WORK_ROOT=/var/lib/curator-worker
CURATOR_REQUIRE_SANDBOX_WORK_ROOT=true
CURATOR_SANDBOX_WORKER_MAX_BODY_BYTES=65536
CURATOR_SANDBOX_WORKER_MAX_CLOCK_SKEW_SECONDS=30
CURATOR_SANDBOX_WORKER_MAX_CONCURRENCY=2
CURATOR_SANDBOX_WORKER_QUEUE_TIMEOUT_SECONDS=1
```

When the worker itself runs in a container while using a host Docker socket, `CURATOR_SANDBOX_WORK_ROOT` must be bind-mounted at the identical absolute path on the host and inside the worker. The worker writes the temporary `input.json` beneath this root and the host daemon bind-mounts that same path into the ephemeral skill container.

Example:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
  - /var/lib/curator-worker:/var/lib/curator-worker
```

Do not map different host and container paths. Docker-outside-of-Docker resolves bind sources on the daemon host, not inside the worker container. `CURATOR_REQUIRE_SANDBOX_WORK_ROOT=true` makes missing or unusable workspace configuration fail readiness and execution closed.

The worker exposes only:

- `GET /health`, open liveness without runtime details.
- `GET /ready`, signed and dependency-aware.
- `POST /v1/execute`, signed sandbox execution.

Interactive API documentation is disabled on the worker.

## Deployment boundary

The Docker socket grants host-equivalent control. Do not mount it into the Curator API container. Run the worker on a dedicated execution host or against a rootless, tightly scoped Docker daemon. Restrict network access so only the Curator API can reach port `8020`.

Do not provide Alpaca, Execution_Agent, Database_Agent or other trading credentials to the worker. The worker accepts only skill code, validated inputs, function name, timeout and skill ID.

The nonce replay cache is process-local. Run one worker replica unless a shared replay store or equivalent edge-level replay control is added.

## Images

Build the control-plane API normally:

```bash
docker build -t curator-agent:<sha> -f Dockerfile .
```

Build the worker image separately:

```bash
docker build -t curator-sandbox-worker:<sha> -f worker.Dockerfile .
```

Build and pre-pull the immutable execution image on the worker host:

```bash
docker build -t curator-skill-sandbox:<sha> -f sandbox/Dockerfile .
```

The worker image contains only the Docker CLI needed to call the controlled daemon. The API image must not contain the CLI or socket.

## Rollout gate

The `Sandbox Worker E2E` GitHub Actions workflow proves that:

1. the API container has no Docker CLI or socket;
2. the worker itself runs in a separate read-only container;
3. the shared work root is visible at the same path to the worker and host daemon;
4. API readiness depends on the signed remote worker;
5. an approved advisory skill executes in an ephemeral hardened container;
6. the temporary workspace is removed after execution;
7. network access, broker access, order placement and fallback remain disabled.

Hourly Paper Trading must keep Curator disabled until this workflow and the cross-repository Manager integration checks pass.
