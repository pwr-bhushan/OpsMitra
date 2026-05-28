# Anomaly Contract

Detectors emit structured anomaly records. These records are the only required input to the local model summarizer.

## Anomaly JSON

```json
{
  "id": "anom_20260528_1015_tenant_acme_sms_abuse",
  "type": "sms_abuse_spike",
  "severity": "high",
  "window_start": "2026-05-28T10:00:00Z",
  "window_end": "2026-05-28T11:00:00Z",
  "tenant_id": "tenant_acme",
  "subject": {
    "api_key_id": "key_sms_prod",
    "endpoint": "/sms/send"
  },
  "observed": {
    "count": 5021,
    "cost_units": 5021.0
  },
  "baseline": {
    "p50": 48.0,
    "p95": 91.0
  },
  "ratio": 55.17,
  "evidence": {
    "sample_request_ids": ["req_001", "req_002"],
    "query": "SELECT ...",
    "notes": ["single API key contributed most of the traffic"]
  },
  "recommended_actions": [
    "Temporarily throttle the API key",
    "Inspect destination numbers",
    "Notify tenant owner"
  ]
}
```

## Fields

| Field | Type | Required | Notes |
|---|---:|---:|---|
| `id` | string | yes | Stable anomaly identifier. |
| `type` | string | yes | Detector-specific anomaly type. |
| `severity` | string | yes | `low`, `medium`, `high`, or `critical`. |
| `window_start` | string | yes | ISO-8601 UTC window start. |
| `window_end` | string | yes | ISO-8601 UTC window end. |
| `tenant_id` | string | no | Tenant involved, if applicable. |
| `subject` | object | yes | Main entity: API key, IP, endpoint, provider, etc. |
| `observed` | object | yes | Observed metric values. |
| `baseline` | object | no | Baseline values used for comparison. |
| `ratio` | number | no | Observed-to-baseline ratio when meaningful. |
| `evidence` | object | yes | Bounded evidence for human/model explanation. |
| `recommended_actions` | array | yes | Short action list. |

## Severity Guidance

- `low`: unusual but likely informational.
- `medium`: worth reviewing during normal work.
- `high`: likely active incident or expensive abuse.
- `critical`: urgent, broad impact, or runaway cost/security exposure.

## Model Input Boundary

The summarizer may receive the full anomaly JSON. It must not receive the full raw event dataset.

Evidence should be bounded:

- include counts, ratios, baselines, identifiers, and a few sample request IDs
- do not include request bodies, auth headers, customer message content, or secrets

## Detector Types for v0

- `sms_abuse_spike`
- `auth_failure_burst`
- `endpoint_error_rate_spike`
- `cost_runaway_usage`
