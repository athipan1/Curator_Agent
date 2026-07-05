# Backtest Approval Gate

Curator can read skill backtest status from Database_Agent and use it as a promotion gate.

## New endpoints

```http
GET /skills/{skill_id}/backtest-status
POST /skills/{skill_id}/approve-from-backtest
```

## Recommendation option

`POST /skills/recommend` now accepts:

```json
{
  "require_backtest_passed": true
}
```

When this flag is enabled, skills without `passed=true` from Database_Agent are excluded from the recommendation list.

## Database dependency

Curator reads:

```http
GET /skills/{skill_id}/backtest-status
```

from Database_Agent.

Required environment variables:

```bash
DATABASE_AGENT_URL=http://database-agent:8004
DATABASE_AGENT_API_KEY=dev_database_key
```

## Safety

This feature only reads stored backtest status and updates Curator skill lifecycle state. It does not perform external execution actions.
