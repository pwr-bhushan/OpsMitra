# AWS Cost Controls

OpsMitra interacts with Athena and S3 in a way that can generate unexpected AWS
charges if partition predicates are omitted or scan limits are not configured.
This document describes the controls that bound query cost.

---

## Partition Strategy

OpsMitra stores events in S3 using a 5-key Hive partition layout:

```
s3://<bucket>/<prefix>/tenant=<t>/year=<Y>/month=<M>/day=<D>/hour=<H>/events-<batch_id>.jsonl.gz
```

The five partition keys are: `tenant`, `year`, `month`, `day`, `hour`.

### Why Partition Predicates Are Mandatory

Athena charges $5 per TB of data scanned. A full-table scan on a year of events
for all tenants could easily cost hundreds of dollars per query.

**Without predicates (full-table scan):**

```sql
-- Scans ALL partitions — potentially TBs
SELECT * FROM opsmitra.events WHERE tenant = 'acme';
```

**With predicates (partition-pruned scan):**

```sql
-- Scans only the exact hours in the requested window
SELECT * FROM opsmitra.events
WHERE tenant    = 'acme'
  AND year      = '2026'
  AND month     = '06'
  AND day       = '01'
  AND hour IN ('00', '01', '02', '03');
```

The query builder in `opsmitra.aws.query_builder` enforces partition predicates
programmatically. It enumerates partitions per-hour for windows ≤ 24 hours and
per-day for windows > 24 hours. Callers cannot produce a predicate-free query
via the public API — missing required arguments raise `ValueError` at construction
time.

---

## Athena Scan Bytes

### How to Estimate

Bytes scanned ≈ Σ(compressed sizes of all S3 objects in matching partitions).

For a 1-hour window over a single tenant with typical synthetic log volume
(~10 000 events × ~200 bytes/event gzipped ~20:1):

```
raw bytes  = 10 000 × 200  =   2 000 000 bytes  =  ~2 MB
gzip ratio ≈ 20:1          → ~100 KB per partition
cost @ $5/TB               = $5 / (1 000 000 MB) × 0.1 MB ≈ $0.000 000 50
```

At v0 synthetic-log scale, per-query costs are negligible. The controls here
matter once real production data scales to GB/day.

### Inspecting Actual Bytes Scanned

After each query, Athena records `DataScannedInBytes` in the query execution:

```python
result = athena_client.get_query_execution(QueryExecutionId=qid)
bytes_scanned = result["QueryExecution"]["Statistics"]["DataScannedInBytes"]
```

Log this value in production to catch regressions before they appear on the bill.

### Minimum Charge

Athena charges a minimum of 10 MB per query even if the actual scan is smaller.
For very small windows (< 10 MB of matching data), the effective cost is the
10 MB minimum.

---

## S3 Storage Classes

### Hot Partitions (< 30 days)

Use **S3 Intelligent-Tiering** for recent partitions. Intelligent-Tiering
automatically moves objects between frequent-access and infrequent-access tiers
at no retrieval cost, which is ideal for event data that is queried heavily in
the first week then rarely afterward.

### Cold Partitions (> 30 days)

Use **S3 Glacier Instant Retrieval** for partitions older than 30 days if replay
latency tolerance is a few milliseconds (Glacier IR retrieval is near-instant).
For archival partitions that are never queried operationally, use **Glacier
Flexible Retrieval** to reduce storage cost further.

### Sample Lifecycle Rule

```json
{
  "Rules": [
    {
      "ID": "opsmitra-tiering",
      "Status": "Enabled",
      "Filter": { "Prefix": "opsmitra-events/" },
      "Transitions": [
        { "Days": 0,  "StorageClass": "INTELLIGENT_TIERING" },
        { "Days": 30, "StorageClass": "GLACIER_IR" }
      ]
    }
  ]
}
```

---

## Workgroup Configuration

Set `BytesScannedCutoffPerQuery` on the Athena workgroup as a hard guard against
runaway queries. This causes Athena to cancel any query that would scan more than
the specified byte threshold.

### Sample Workgroup (AWS CLI)

```bash
aws athena create-work-group \
  --name opsmitra \
  --configuration '{
    "ResultConfiguration": {
      "OutputLocation": "s3://your-athena-output-bucket/results/",
      "EncryptionConfiguration": {
        "EncryptionOption": "SSE_S3"
      }
    },
    "BytesScannedCutoffPerQuery": 1073741824,
    "EnforceWorkGroupConfiguration": true,
    "PublishCloudWatchMetricsEnabled": true
  }'
```

`BytesScannedCutoffPerQuery` is set to 1 GB (1 073 741 824 bytes) — adjust based
on your expected partition sizes and cost tolerance. `EnforceWorkGroupConfiguration`
prevents clients from overriding the byte cap.

---

## Cost-Control Env Vars

The following environment variables limit resource consumption within OpsMitra's
in-process controls. These complement (not replace) the workgroup byte cap.

| Env Var | Type | Default | Purpose |
|---------|------|---------|---------|
| `OPSMITRA_MAX_EVENTS_PER_WINDOW` | `int` | `1000000` | Maximum events fetched per `run` window. Prevents the detector from processing an unbounded number of events from large partitions. Raises `ValueError` if set ≤ 0. |
| `OPSMITRA_ATHENA_MAX_BYTES_SCANNED` | `int` | *(future)* | Per-query scan byte cap enforced by OpsMitra before submitting the query. Currently not implemented; the workgroup-level `BytesScannedCutoffPerQuery` serves as the primary guard. |

### Dogfooding the Cost-Runaway Detector

OpsMitra includes a `detect_cost_runaway` detector that watches for sudden spikes
in a `cost_meter` event stream. This same detector can be pointed at Athena's own
CloudWatch `BytesScannedByQuery` metric (published when `PublishCloudWatchMetricsEnabled`
is true) to alert when query scan costs exceed a threshold — effectively using
OpsMitra to monitor its own Athena spend.

See `OPSMITRA_THRESHOLDS_PATH` to configure the `cost_runaway_delta_usd` threshold
and `cost_runaway_window_seconds` window for this use case.
