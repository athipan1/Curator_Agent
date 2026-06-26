# Curator Agent

Curator Agent is a safe registry service for reusable trading-analysis skills in the multi-agent trading system.

The MVP is intentionally conservative: it can store and validate candidate Python skills, but it does **not** execute stored code. This keeps the first version safe for a trading stack while still creating the foundation for skill curation, retrieval, audit, and later sandboxed execution.

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
Manager Agent / Orchestrator
        ↓ retrieves suitable validated skill
Safe Sandbox Runner, future phase
        ↓ emits signal only, never places orders directly
Risk Agent → Execution Agent → Broker
```

## MVP endpoints

```text
GET  /health
POST /skills/register
GET  /skills
GET  /skills/search?q=...
GET  /skills/{skill_id}
```

## Safety rules in MVP

The static validator rejects obvious unsafe Python constructs:

- `import` / `from import`
- `global` / `nonlocal`
- `eval`, `exec`, `compile`, `open`, `input`, `__import__`
- dangerous calls like `os.system`, `subprocess.run`, `shutil.rmtree`
- dunder names/functions
- code with no function definition

Rejected skills are still stored with `validation_status="rejected"` so they can be audited.

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

## Next phases

1. Add semantic skill search.
2. Add signed skill versions and promotion workflow: `draft → validated → approved → deprecated`.
3. Add isolated Docker sandbox execution with no broker credentials and signal-only output.
4. Add Manager_Agent integration to query validated skills by market context.
5. Add Skill performance tracking after backtests and paper-trade observation.
