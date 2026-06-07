# OpsMitra Security Checklist

This checklist covers the security posture of OpsMitra across IAM, secret handling,
model data exposure, Athena cost risks, and operational failure modes. Review before
every deployment and after every config change.

---

## IAM

OpsMitra requires the narrowest possible IAM permissions. Never use `*` actions or
resources in production.

### Minimum IAM Policy (Athena + S3)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AthenaQueryExecution",
      "Effect": "Allow",
      "Action": [
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults"
      ],
      "Resource": "arn:aws:athena:us-east-1:123456789012:workgroup/opsmitra"
    },
    {
      "Sid": "S3AthenaOutputRead",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-athena-output-bucket",
        "arn:aws:s3:::your-athena-output-bucket/*"
      ]
    },
    {
      "Sid": "S3EventsWrite",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::your-events-bucket/opsmitra-events/*"
    },
    {
      "Sid": "GlueMetadata",
      "Effect": "Allow",
      "Action": [
        "glue:GetTable",
        "glue:GetPartitions"
      ],
      "Resource": [
        "arn:aws:glue:us-east-1:123456789012:catalog",
        "arn:aws:glue:us-east-1:123456789012:database/opsmitra",
        "arn:aws:glue:us-east-1:123456789012:table/opsmitra/events"
      ]
    }
  ]
}
```

### Principle of Least Privilege

- Grant only the actions listed above — nothing else.
- Scope resource ARNs to specific workgroup, bucket, and database names.
- Do not use `arn:aws:*` wildcards; use explicit resource ARNs.
- Rotate access keys every 90 days or use instance roles / IRSA (no long-lived keys).

### AssumeRole Pattern for Cross-Account

For cross-account scenarios, prefer `sts:AssumeRole` with an external ID:

```json
{
  "Effect": "Allow",
  "Action": "sts:AssumeRole",
  "Resource": "arn:aws:iam::TARGET_ACCOUNT_ID:role/OpsMitraReadRole",
  "Condition": {
    "StringEquals": { "sts:ExternalId": "opsmitra-prod-external-id" }
  }
}
```

Never embed the external ID in source code — keep it in AWS Secrets Manager or
an environment variable.

---

## Secret Handling

### Env-Var Only

All secrets are loaded from environment variables, never from source code or config
files committed to version control.

| Secret | Env Var | Notes |
|--------|---------|-------|
| Slack webhook URL | `OPSMITRA_SLACK_WEBHOOK_URL` | Never log; redacted in all exception paths |
| AWS access key | `AWS_ACCESS_KEY_ID` | Prefer instance role or IRSA |
| AWS secret | `AWS_SECRET_ACCESS_KEY` | Prefer instance role or IRSA |
| AWS session token | `AWS_SESSION_TOKEN` | When using AssumeRole |

### No Commits

- Add `*.env`, `.env.local`, and `*.secret` to `.gitignore`.
- Pre-commit hook: `grep -r "OPSMITRA_SLACK_WEBHOOK_URL=" .` should return nothing in staged files.
- Never hardcode account IDs, webhook URLs, or API keys in tests — use placeholder strings like `https://hooks.slack.com/test-only/xxx`.

### `_redact()` Pattern

`slack_alerter.py` uses a `_redact(url)` helper that replaces the webhook URL with
`[REDACTED]` before any exception is raised or logged. The redaction happens **before**
branching on exception subtype so that `HTTPError` (subclass of `URLError`) cannot
silently produce a leak via an unredacted branch.

### `AthenaQueryError` Redaction

`AthenaQueryError` carries only the Athena query execution ID and a safe status
message — it never echoes the original SQL string or the Athena output S3 path
(which encodes the account and bucket name).

---

## Model Data Exposure

### Scoped Evidence Only

The summarizer receives at most 480 characters of structured anomaly evidence:

- anomaly type, tenant ID, subject, counts, ratios
- NO raw event payloads
- NO request bodies, authorization headers, or customer message content
- NO webhook URLs or other secrets

This is enforced in `Summarizer.summarize()` by the `evidence` field truncation
before the prompt is constructed.

### Webhook URL Never Logged

The Slack webhook URL is never passed to or logged by the model subsystem.
`Runtime._log_event()` emits only `key=value` structured fields and explicitly
excludes the alert config's `slack_webhook_url` field.

### Privacy Boundary Summary

| Data | Allowed in prompt | Rationale |
|------|-------------------|-----------|
| Anomaly type + counts | Yes | Core signal; no PII |
| Tenant ID | Yes | Needed for triage |
| Subject (endpoint / API key) | Yes | Needed for triage |
| Raw log lines | No | Volume risk + PII risk |
| Webhook URL | No | Secret |
| AWS account ID | No | Operational secret |
| Customer message bodies | No | PII/compliance risk |

---

## Athena Cost Risks

### Partition Predicates Are Mandatory

OpsMitra's Athena query builder always emits `WHERE` clauses scoped to the
exact partitions covering the requested window:

```sql
-- Good — partition-predicate included, only scans matching hour partitions
SELECT * FROM opsmitra.events
WHERE tenant = 'acme'
  AND year = '2026' AND month = '06' AND day = '01' AND hour = '14'

-- Bad — full table scan, scans ALL partitions
SELECT * FROM opsmitra.events
WHERE tenant = 'acme'
```

The query builder in `opsmitra.aws.query_builder` enforces predicates programmatically;
callers cannot produce a predicate-free query via the public API.

See `docs/aws-cost-controls.md` for per-query byte-cap configuration.

### `_MAX_EVENTS_FILE_BYTES` Cap

`S3NDJSONEventSink` caps per-partition gzip bodies at 100 MB (`_MAX_GZIP_BYTES`).
A warning is issued at 50 MB to give time to flush. Exceeding the cap raises
`ValueError` rather than silently writing an over-sized object.

### Mandatory Workgroup Byte Cap

Set `BytesScannedCutoffPerQuery` on the Athena workgroup as a defense-in-depth
guard against runaway queries. See `docs/aws-cost-controls.md` § Workgroup Configuration.

---

## Operational Failure Modes

The table below maps each known failure mode to the component, behavior, log signal,
and recovery path.

| Failure Mode | Component | Behavior | Log Signal | Recovery |
|---|---|---|---|---|
| Cooldown file corrupt / malformed JSON | `AnomalyCooldown` | Silent reset to empty state + `WARNING` log | `WARNING cooldown file malformed; resetting` | Delete corrupt file; cooldown will rebuild. Investigate writer race (Step 10: `fcntl` lock). |
| Slack outage — 5xx / network error | `SlackAlerter` | Exponential backoff `0.5/1/2s`; up to 3 retries; total wall-clock cap 15s; then appends to `errors` list | `WARNING slack delivery failed` | Transient: retries self-heal. Persistent: check Slack status page; anomaly is still detected and logged. |
| Slack 4xx / 429 (rate limit) | `SlackAlerter` | Fail-fast (no retries); `DeliveryResult.ok=False`; appended to `errors` | `WARNING slack 4xx/429 fail-fast` | Fix webhook URL (4xx) or reduce alert frequency via cooldown TTL (429). |
| Athena query failure | `AthenaEventSource` | Raises `AthenaQueryError` with query-exec-ID and safe status string; Runtime catches and appends to `errors` | `ERROR athena query failed` | Check Athena workgroup; verify DDL/partition columns; confirm IAM policy. |
| Model timeout / unavailable | `Summarizer` | Routed to `FallbackSummarizer`; produces `confidence="low"` deterministic summary | `WARNING summarizer fallback` | Check local Ollama endpoint; `FallbackSummarizer` keeps pipeline running. |
| Threshold misconfiguration | `load_thresholds` | Raises `ValueError` at config load time with field name | `ERROR unknown threshold field: ...` | Fix the JSON override file; field names must match `DetectorThresholds` exactly. |
| Cooldown concurrent writer (single-host) | `AnomalyCooldown` | `fcntl` LOCK_EX NB; on `BlockingIOError`: `WARNING` and skip persist | `WARNING cooldown persist skipped: another writer holds the lock` | Single-process v0: this should not occur. Multi-host future: replace with DynamoDB backend. |

### Pre-Commit Security Checklist

Before every commit:

- [ ] No `OPSMITRA_SLACK_WEBHOOK_URL` value in staged files (only env-var references)
- [ ] No real AWS account IDs in test fixtures
- [ ] No hardcoded secrets in `src/` or `tests/`
- [ ] `_redact()` covers all exception branches that could surface the webhook URL
- [ ] Athena queries in `query_builder.py` always include partition predicates
- [ ] `AthenaQueryError` message does not echo SQL or S3 output path
- [ ] Model prompt uses only scoped evidence (≤480 chars), not raw event content
- [ ] Dry-run defaults to `true`; CI never sets `OPSMITRA_SLACK_DRY_RUN=false`
