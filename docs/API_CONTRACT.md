# Curator_Agent API Contract

This document defines the baseline API contract for `Curator_Agent`.

`Curator_Agent` provides skill registry, skill lifecycle, skill search, controlled skill execution, and performance policy curation output for other agents.

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

## Notes

1. Runtime readiness is reported through `/ready`.
2. Version and schema metadata are reported through `/version`.
3. Existing curator endpoints keep their current response models.
4. Existing skill lifecycle and execution checks remain responsible for runtime behavior.
