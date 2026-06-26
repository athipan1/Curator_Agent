# Curator Agent

Curator Agent is a safe registry service for reusable trading-analysis skills in the multi-agent trading system.

The first phase stores, validates, and approves candidate Python skills. The current MVP also adds a conservative sandbox execution endpoint for **approved signal-only skills**. Curator does not place broker orders and does not expose broker credentials to stored code.

## Why this exists

A trading agent stack can waste time and tokens repeatedly asking LLMs to rediscover the same logic. Curator Agent stores reusable skills such as:

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
Safe Sandbox Runner
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
- `approval_status=approved` means the skill has been reviewed and can be retrieved/executed by production agents.
- `approval_status=deprecated` means the skill should no longer be selected for new use.

Only `validated` skills can be approved. Only `validated + approved` skills can execute.

## Safety rules

The static validator rejects obvious unsafe Python constructs:

- `import` / `from import`
- `global` / `nonlocal`
- `eval`, `exec`, `compile`, `open`, `input`, `__import__`
- dangerous calls like `os.system`, `subprocess.run`, `shutil.rmtree`
- dunder names/functions
- code with no function definition

Rejected skills are still stored with `validation_status="rejected"` so they can be audited.

The execution sandbox is intentionally narrow:

- runs in a short-lived restricted process
- no broker credentials are injected
- no network clients are injected
- no file IO helpers are available
- timeout is capped between `0.1` and `5.0` seconds
- skill output must be a JSON object
- broker/order-like output keys such as `order_id`, `broker_order_id`, and `risk_approval_id` are rejected

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

## Run tests

```bash
PYTHONPATH=. python -m pytest -q
```

## Docker

```bash
docker build -t curator-agent .
docker run --rm -p 8010:8010 -v curator-data:/data curator-agent
```

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

Expected response shape:

```json
{
  "status": "success",
  "agent_type": "curator-agent",
  "version": "0.1.0",
  "data": {
    "execution_status": "success",
    "output": {"signal": "buy", "confidence": 0.7},
    "safety": {
      "broker_access": false,
      "network_access": false,
      "file_access": false,
      "order_placement": false
    }
  }
}
```

## Next phases

1. Add semantic skill search.
2. Add signed skill versions.
3. Move sandbox execution into a fully isolated Docker runtime profile.
4. Add Manager_Agent integration to query approved skills by market context.
5. Add Skill performance tracking after backtests and paper-trade observation.
