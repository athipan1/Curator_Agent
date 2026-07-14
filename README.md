# Curator Agent

Curator Agent is a registry and isolated execution service for reusable trading-analysis
skills in the multi-agent trading system.

The service stores, validates, and approves candidate Python skills. Approved signal-only
skills execute in an ephemeral Docker container by default. Curator does not place broker
orders and does not expose broker credentials to stored code.

## Why this exists

A trading agent stack can waste time and tokens repeatedly asking LLMs to rediscover the
same logic. Curator Agent stores reusable skills such as:

- technical signal functions
- market-regime filters
- scoring helpers
- data-normalization helpers
- future connector scripts after sandbox review

The intended flow is:

```text
Research/Technical/Fundamental Agent
        ↓ proposes pure Python skill
Curator Agent
        ↓ validates + stores + indexes metadata
Human/Risk owner
        ↓ approves safe, reviewed skills
Manager Agent / Orchestrator
        ↓ retrieves approved skills by market context
Isolated Container Sandbox (default)
        ↓ emits signal only, never places orders directly
Risk Agent → Execution Agent → Broker
```

## MVP endpoints

```text
GET  /health
POST /skills/register
GET  /skills?tag=...&validation_status=...&approval_status=...
GET  /skills/search?q=...&approval_status=...
GET  /skills/{skill_id}
POST /skills/{skill_id}/approve
POST /skills/{skill_id}/deprecate
POST /skills/{skill_id}/execute
```

## Skill lifecycle

```text
draft → approved → deprecated
```

Validation and approval are separate:

- `validation_status=validated` means the static safety validator passed.
- `validation_status=rejected` means the skill failed static safety validation.
- `approval_status=draft` means the skill is stored but not production-approved.
- `approval_status=approved` means the skill has been reviewed and can execute.
- `approval_status=deprecated` means the skill should no longer be selected for new use.

Only `validated` skills can be approved. Only `validated + approved` skills can execute.

## Security and isolation

Container isolation is enabled by default:

```bash
CURATOR_CONTAINER_SANDBOX_ENABLED=true
CURATOR_CONTAINER_SANDBOX_FALLBACK=false
CURATOR_SANDBOX_IMAGE=curator-skill-sandbox:latest
```

Production must provide a working Docker daemon and the configured sandbox image. The
container retains all existing hardening controls:

- `--network=none`
- a read-only root filesystem
- `--cap-drop=ALL`
- `no-new-privileges`
- CPU, memory, and PID limits
- an unprivileged user and restricted temporary filesystem

If Docker or the image is unavailable, or container startup fails, the request fails closed
with `execution_status="rejected_no_isolated_sandbox"`. Skill code is not executed by the
process runner.

### Explicit compatibility fallback

`CURATOR_CONTAINER_SANDBOX_FALLBACK=true` permits an intentional downgrade to the
process-level runner when container execution is unavailable or fails. This setting is
high-risk and should be temporary. Every use:

- emits a `CRITICAL` log containing `SECURITY ALERT`
- increments `curator_container_sandbox_fallback_total`
- returns `fallback_used=true` and `security_alert="isolated_sandbox_fallback_used"`

The fallback runner uses restricted builtins, a separate process, timeouts, and AST checks
that reject common introspection paths such as `__class__`, `__bases__`, `__subclasses__`,
and `__globals__`. These controls are defense in depth only. Python `exec()` restrictions
are **best-effort isolation, not a true sandbox**, and do not provide a security guarantee
against hostile code.

Setting `CURATOR_CONTAINER_SANDBOX_ENABLED=false` is an explicit operator opt-out and uses
the same best-effort process runner without first attempting container execution.

The static validator also rejects obvious unsafe Python constructs:

- `import` / `from import`
- `global` / `nonlocal`
- `eval`, `exec`, `compile`, `open`, `input`, `__import__`
- dangerous calls like `os.system`, `subprocess.run`, `shutil.rmtree`
- dunder names/functions
- code with no function definition

Rejected skills remain stored with `validation_status="rejected"` for auditability.

Curator should emit **signals only**, for example:

```json
{
  "signal": "buy",
  "confidence": 0.7,
  "reason": "RSI oversold"
}
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8010
```

The secure default requires Docker and the sandbox image even for local skill execution.
Build the image before calling the execution endpoint:

```bash
docker build -t curator-skill-sandbox:latest -f sandbox/Dockerfile .
```

For local compatibility testing only, operators may intentionally opt out with
`CURATOR_CONTAINER_SANDBOX_ENABLED=false`. Do not use that setting for untrusted code.

## Run tests

```bash
PYTHONPATH=. python -m pytest -q
```

Tests explicitly opt out of Docker unless they are verifying the container execution path.
Dedicated tests remove that override and assert the secure production defaults.

## Docker

```bash
docker build -t curator-agent .
docker run --rm -p 8010:8010 -v curator-data:/data curator-agent
```

Running Curator itself in a container does not automatically make the sandbox available.
Production must also provide a controlled Docker execution facility and the
`curator-skill-sandbox:latest` image. If it does not, all skill execution is rejected by
default instead of silently downgrading isolation.

## Example skill registration

```bash
curl -X POST http://localhost:8010/skills/register \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "RSI Momentum Filter",
    "description": "Simple RSI signal helper for technical analysis.",
    "tags": ["technical", "rsi"],
    "market_context": {"asset_class": "stocks", "regime": "momentum"},
    "code": "def rsi_signal(rsi_value):\n    if rsi_value < 30:\n        return {\"signal\": \"buy\", \"confidence\": 0.7}\n    if rsi_value > 70:\n        return {\"signal\": \"sell\", \"confidence\": 0.7}\n    return {\"signal\": \"hold\", \"confidence\": 0.5}"
  }'
```

## Example approval

```bash
curl -X POST http://localhost:8010/skills/<skill_id>/approve \
  -H 'Content-Type: application/json' \
  -d '{"approved_by": "risk-owner", "reason": "Reviewed for paper-trading use"}'
```

## Example sandbox execution

```bash
curl -X POST http://localhost:8010/skills/<skill_id>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": {"rsi_value": 25},
    "timeout_seconds": 1.0
  }'
```

Expected successful response data includes the container security metadata:

```json
{
  "execution_status": "success",
  "output": {"signal": "buy", "confidence": 0.7},
  "sandbox": {
    "mode": "container",
    "network_access": false,
    "read_only_filesystem": true,
    "capabilities_dropped": true,
    "no_new_privileges": true
  }
}
```

## Next phases

1. Add semantic skill search.
2. Add signed skill versions.
3. Export fallback counters through the shared observability backend.
4. Add Manager_Agent integration to query approved skills by market context.
5. Add skill performance tracking after backtests and paper-trade observation.
