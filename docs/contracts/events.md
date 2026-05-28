# Event Contract

OpsMitra v0 events represent synthetic SaaS API activity. The schema is intentionally small and structured so deterministic detectors can operate without parsing free-form log text.

## Event JSON

```json
{
  "timestamp": "2026-05-28T10:15:00Z",
  "tenant_id": "tenant_acme",
  "user_id": "user_123",
  "api_key_id": "key_sms_prod",
  "endpoint": "/sms/send",
  "method": "POST",
  "status_code": 200,
  "latency_ms": 82,
  "ip": "203.0.113.10",
  "country": "IN",
  "cost_units": 1.0,
  "provider": "twilio",
  "request_id": "req_123"
}
```

## Fields

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `timestamp` | string | yes | ISO-8601 UTC timestamp. |
| `tenant_id` | string | yes | Synthetic tenant identifier. |
| `user_id` | string | no | Synthetic user identifier when applicable. |
| `api_key_id` | string | no | Synthetic API key identifier. |
| `endpoint` | string | yes | API route or logical operation. |
| `method` | string | yes | HTTP method or operation verb. |
| `status_code` | integer | yes | HTTP-like status code. |
| `latency_ms` | integer | no | Request latency in milliseconds. |
| `ip` | string | no | Client IP. Synthetic only in v0. |
| `country` | string | no | ISO-like country code. |
| `cost_units` | number | yes | Unit count for cost-like activity. |
| `provider` | string | no | External service or internal subsystem. |
| `request_id` | string | yes | Unique event/request identifier. |

## Invariants

- `timestamp`, `tenant_id`, `endpoint`, `method`, `status_code`, `cost_units`, and `request_id` are required.
- `cost_units` must be zero or positive.
- `status_code` should be between 100 and 599.
- `timestamp` must be normalized to UTC.
- Generator output must be deterministic when a seed is provided.

## Local File Format

Local logs use newline-delimited JSON:

```text
{"timestamp":"2026-05-28T10:15:00Z",...}
{"timestamp":"2026-05-28T10:15:01Z",...}
```

## Athena Table Shape

The AWS mode should preserve the same logical fields. Partition columns should be separate from the event timestamp:

```text
dt=2026-05-28/hour=10/
```

Queries must filter by `dt` and, when possible, `hour`.
