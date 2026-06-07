# AWS Window Replay Exercise

This document is the primary learning milestone of the OpsMitra project. The goal
is to run OpsMitra's detectors against a real (or realistic) window of AWS log data
and observe how the detector pipeline behaves with non-synthetic input.

After completing this exercise, fill in `docs/aws-window-replay-findings.md` with
your observations, false positives, and suggested threshold adjustments.

---

## Prerequisites

Before starting, confirm you have:

1. **AWS account** with access to the target data source.
2. **S3 bucket** that holds the log data (or will hold synthetic events from Step 7).
3. **Athena workgroup** configured with a result output location and byte-scan cutoff.
   See `docs/aws-cost-controls.md` § Workgroup Configuration.
4. **IAM role or user** with the minimum permissions from `docs/security-checklist.md` § IAM.
5. **Glue Data Catalog table** named `opsmitra.events` (or equivalent) with the
   5-key partition schema: `tenant / year / month / day / hour`.
6. **Local `.venv` set up** with OpsMitra installed:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -e .
   ```
7. AWS credentials available via environment variables, `~/.aws/credentials`, or
   an instance profile — OpsMitra uses `boto3`'s standard credential chain.

---

## Prepare Window

Choose a 1–4 hour window of real log data. Good sources include:

- **OpsMitra's own generated events** already written to S3 from a prior `run` command.
- **CloudTrail management events** (API calls made in your account).
- **S3 access logs** for a busy bucket.
- **Generic API access logs** written by your application.

### Sample CloudTrail-to-OpsMitra Event Mapping

If using CloudTrail, map each `eventName` to an OpsMitra event type:

| CloudTrail `eventName` | OpsMitra `event_type` | Notes |
|---|---|---|
| `SendMessage` (SNS/SQS) | `sms_send` | Map SNS publish calls |
| `ConsoleLogin`, `AssumeRole` | `auth_login` | Login + federation events |
| `InvokeAPI`, `Invoke` | `api_request` | Lambda / API Gateway calls |
| Cost Explorer `GetCostAndUsage` | `cost_meter` | Billing query calls |

Adapt this mapping to your actual log schema. The `tenant` field should identify
the AWS account, team, or application generating the events.

### Minimum Event Schema

Each line in your events JSONL must contain at least:

```json
{
  "event_type": "auth_login",
  "tenant": "my-account",
  "timestamp": "2026-06-01T14:32:00Z",
  "subject": "user@example.com"
}
```

Additional fields are allowed and ignored by the detector.

---

## Configure Environment

Export the required environment variables before running OpsMitra in AWS mode:

```bash
export OPSMITRA_EVENT_SOURCE=athena
export OPSMITRA_AWS_REGION=us-east-1
export OPSMITRA_S3_BUCKET=your-events-bucket
export OPSMITRA_S3_PREFIX=opsmitra-events
export OPSMITRA_ATHENA_DATABASE=opsmitra
export OPSMITRA_ATHENA_TABLE=events
export OPSMITRA_ATHENA_WORKGROUP=opsmitra
export OPSMITRA_ATHENA_OUTPUT_LOCATION=s3://your-athena-output-bucket/results/
export OPSMITRA_DRY_RUN=true

# Optional: custom thresholds tuned to your data
# export OPSMITRA_THRESHOLDS_PATH=./my-thresholds.json

# Optional: point at a local Ollama model for summaries
# export OPSMITRA_MODEL_URL=http://localhost:11434
# export OPSMITRA_MODEL_NAME=llama3.1:8b
```

Keep `OPSMITRA_DRY_RUN=true` for the replay exercise to avoid sending real Slack
alerts while you are tuning thresholds.

Before the first Athena round-trip, run the **preflight validator** to confirm
the AWS env vars are wired correctly:

```bash
.venv/bin/python -m opsmitra validate --mode aws-source --strict
```

Exit `0` means every required AWS env var is set; exit `1` means something
is missing (e.g. you forgot `OPSMITRA_ATHENA_OUTPUT_LOCATION`). This costs
nothing — it makes no network call.

---

## Run Detector

Run the detector against your chosen window:

```bash
.venv/bin/python -m opsmitra run \
  --window-start <ISO8601> \
  --window-end   <ISO8601> \
  --source athena \
  --dry-run
```

Example for a 2-hour window:

```bash
.venv/bin/python -m opsmitra run \
  --window-start 2026-06-01T14:00:00Z \
  --window-end   2026-06-01T16:00:00Z \
  --source athena \
  --dry-run
```

### Expected Exit Codes

| Code | Meaning |
|------|---------|
| `0` | No anomalies detected in the window |
| `2` | One or more anomalies detected (dry-run payload printed to stdout) |
| `1` | Runtime or configuration error (check stderr) |
| `64` | CLI usage error (bad arguments) |

The dry-run Slack payload is printed to stdout as JSON. Capture it:

```bash
.venv/bin/python -m opsmitra run ... --dry-run > replay-output.json; echo "exit: $?"
```

---

## Capture Findings

After the run, fill in `docs/aws-window-replay-findings.md` with:

1. The exact window timestamps and source type.
2. Every anomaly that fired — include the fingerprint and whether it was expected.
3. False positives (anomalies that fired but don't represent real incidents).
4. False negatives (real incidents that did not fire).
5. The Athena bytes-scanned from the query execution statistics.
6. The `detection_delay_upper_bound_seconds` per anomaly.
7. Any threshold overrides you applied, as a JSON snippet.
8. Suggested follow-ups: new detectors, threshold changes, data quality issues.

### Iterating on Thresholds

If the run produces too many false positives, raise the relevant threshold:

```json
{
  "default": {
    "auth_burst_count": 20,
    "sms_abuse_rate_per_minute": 10.0
  }
}
```

Save to a file and set `OPSMITRA_THRESHOLDS_PATH=./my-thresholds.json`, then re-run.

If too few anomalies fire, lower the thresholds or widen the window duration.
