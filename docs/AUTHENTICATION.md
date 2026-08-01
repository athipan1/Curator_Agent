# Curator API Authentication

Curator protects every non-operational endpoint with an `X-API-KEY` credential when authentication is enabled.

Operational endpoints remain open:

- `GET /health`
- `GET /ready`
- `GET /version`

## Configuration

A single compatibility key may be used for every role:

```bash
CURATOR_API_KEY=<shared-secret>
CURATOR_REQUIRE_API_KEY=true
```

Production deployments should use separate credentials:

```bash
CURATOR_READ_API_KEY=<read-secret>
CURATOR_EXECUTE_API_KEY=<execute-secret>
CURATOR_ADMIN_API_KEY=<admin-secret>
CURATOR_REQUIRE_API_KEY=true
```

`APP_ENV=production` or `ENVIRONMENT=production` always requires authentication and fails startup when an effective key is missing.

## Roles

| Role | Allowed operations |
| --- | --- |
| `read` | list, search, detail, recommendation, backtest status, skill-family reads |
| `execute` | every read operation plus skill execution and shadow ensemble |
| `admin` | every operation, including register, approve, deprecate, version, promote, rollback, and policy curation |

Higher privilege keys may call lower privilege endpoints. A read key cannot execute code or mutate lifecycle state.

## Manager_Agent contract

Manager_Agent must send its execute-role credential as:

```http
X-API-KEY: <CURATOR_AGENT_API_KEY>
X-Correlation-ID: <correlation-id>
```

Administrative scripts must use a separate `CURATOR_ADMIN_API_KEY` when role-specific keys are configured.

Development remains backward-compatible when no key is configured and the environment is not production. This compatibility mode must not be used on a network-accessible deployment.
