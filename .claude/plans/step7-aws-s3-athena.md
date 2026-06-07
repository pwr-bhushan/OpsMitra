# Step 7: AWS S3 + Athena Integration

## 1. Requirements Restatement

Add an optional AWS mode that (a) writes generated events to S3 as partitioned NDJSON, (b) reads events back via Athena with partition-aware queries, and (c) externalizes detector thresholds so real Athena data can be tuned without code changes. Local mode must remain fully functional and credential-free.

| AC | Subgoal | Lands in phase |
|----|---------|----------------|
| 1. AWS code is isolated behind interfaces | `EventSource` / `EventSink` Protocols + local impls in `event_source.py`; AWS impls live exclusively under `opsmitra.aws.*` and are only imported when the backend is selected | B, D, E |
| 2. Athena query text is tested for partition filters | Pure `build_event_query()` returns the exact SQL string; tests assert partition predicates per window shape and reject SQL-injection attempts | C |
| 3. Local tests do not require AWS credentials | `boto3` is only imported inside `opsmitra.aws.*`; an isolation test asserts `boto3` not in `sys.modules` after importing `opsmitra.event_source`; AWS tests stub the client via `botocore.stub.Stubber` | B, D, E, F |
| 4. Detector thresholds load from config with per-tenant / per-endpoint overrides | `DetectorThresholds` dataclass + `OPSMITRA_THRESHOLDS_PATH` JSON file + `resolve_thresholds(tenant, endpoint)` precedence: endpoint > tenant > default; detector functions accept thresholds parameter | A |

## 2. File Layout

**New:**

- `src/opsmitra/event_source.py` — `EventSource` Protocol, `EventSink` Protocol, `WriteResult`, `LocalNDJSONEventSource`, `LocalNDJSONEventSink`. No boto3 imports.
- `src/opsmitra/aws/__init__.py` — empty (subpackage marker).
- `src/opsmitra/aws/query_builder.py` — pure `build_event_query(...)`. No boto3 imports (pure str ops).
- `src/opsmitra/aws/athena_source.py` — `AthenaEventSource` implementing `EventSource`. boto3 imported here.
- `src/opsmitra/aws/s3_sink.py` — `S3NDJSONEventSink` implementing `EventSink`. boto3 imported here.

**Modified:**

- `src/opsmitra/config.py` — add `DetectorThresholds` dataclass, `ThresholdsOverrides` (tenants/endpoints maps), `load_thresholds()`, `resolve_thresholds()`. Extend `AwsConfig` with `s3_prefix`, `athena_workgroup`. Extend `AppConfig` with `event_source_kind`, `event_sink_kind`, `local_events_path`, `thresholds`.
- `src/opsmitra/detectors/sms_abuse.py`, `auth_failure.py`, `error_spike.py`, `cost_runaway.py` — accept `thresholds: DetectorThresholds` parameter (defaults preserved when caller passes `DetectorThresholds()`). Read only fields they care about.

**Test files (new):** `tests/test_event_source_local.py`, `tests/test_query_builder.py`, `tests/test_athena_source.py`, `tests/test_s3_sink.py`, `tests/test_detector_thresholds.py`, `tests/test_aws_isolation.py`. Existing detector tests updated minimally to pass `DetectorThresholds()`.

## 3. Protocols

```python
# event_source.py
from typing import Protocol, Iterable, Sequence
from datetime import datetime
from dataclasses import dataclass
from opsmitra.models import Event

class EventSource(Protocol):
    def fetch_events(
        self,
        window_start: datetime,
        window_end: datetime,
        tenant: str | None = None,
        types: Sequence[str] | None = None,
    ) -> Iterable[Event]: ...

@dataclass(frozen=True)
class WriteResult:
    written: int
    partitions: list[str]  # e.g. ["tenant=acme/year=2026/month=06/day=06/hour=12", ...]

class EventSink(Protocol):
    def write_events(self, events: Iterable[Event]) -> WriteResult: ...
```

Both local impls are constructed with a `Path` (`LocalNDJSONEventSource(path)`, `LocalNDJSONEventSink(path)`). Source filters in-memory by window/tenant/types; sink appends NDJSON and returns a single synthetic partition string.

## 4. Athena Schema

Storage: S3 NDJSON with gzip, JSON SerDe (`org.openx.data.jsonserde.JsonSerDe`).

**S3 prefix layout:**

```
s3://{bucket}/{prefix}/tenant={t}/year={YYYY}/month={MM}/day={DD}/hour={HH}/events.jsonl.gz
```

**CREATE EXTERNAL TABLE DDL (reference, not auto-run):**

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS opsmitra.events (
  timestamp     string,
  tenant_id     string,
  user_id       string,
  api_key_id    string,
  endpoint      string,
  method        string,
  status_code   int,
  latency_ms    int,
  ip            string,
  country       string,
  cost_units    double,
  provider      string,
  request_id    string
)
PARTITIONED BY (
  tenant string,
  year   string,
  month  string,
  day    string,
  hour   string
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://{bucket}/{prefix}/'
TBLPROPERTIES ('has_encrypted_data'='false', 'compressionType'='gzip');
```

13 data columns mirror `Event.to_dict()`. 5 partition keys (`tenant, year, month, day, hour`), all strings — keeps the projection language and predicate generation uniform.

## 5. Query Builder Contract

```python
def build_event_query(
    table: str,                            # "opsmitra.events"
    window_start: datetime,                # UTC
    window_end: datetime,                  # UTC, exclusive
    tenant: str | None = None,
    types: Sequence[str] | None = None,    # endpoint whitelist (e.g. ["/sms/send"])
) -> str
```

**Predicate generation:**

- Window <= 24h: enumerate `(year, month, day, hour)` tuples covering `[window_start, window_end)` at hour granularity. Emit `WHERE (year='YYYY' AND month='MM' AND day='DD' AND hour='HH') OR (...)`.
- Window > 24h: enumerate `(year, month, day)` triples; drop the hour predicate (full-day scan per partition — noted as future optimization). Comment in the SQL: `-- partition pruning at day granularity`.
- Always append `AND timestamp >= '<iso>' AND timestamp < '<iso>'` to bound rows inside boundary partitions.

**Tenant filter:** `re.fullmatch(r"[a-zA-Z0-9_\-]+", tenant)` MUST match, else raise `ValueError("tenant contains unsafe characters")`. On match, append `AND tenant = '<tenant>'` (also append `AND tenant_id = '<tenant>'` to filter within partition). No string concat of unvalidated input.

**Types filter:** each value MUST appear in a whitelist (the four canonical endpoints used by generator + detectors: `/sms/send`, `/auth/login`, `/checkout`, `/jobs/process`). Reject otherwise with `ValueError("type not in allowed set: <name>")`. Then emit `AND endpoint IN ('a', 'b')` from the validated list.

**Choice (named params vs whitelist):** named parameters in `StartQueryExecution` add wiring complexity for the local query-builder unit tests (string is no longer self-contained). Picked **whitelist + regex + enum filter** because (a) inputs are tightly bounded (tenant IDs are internally generated; endpoints are a closed enum), (b) test assertion `"DROP" not in sql` becomes trivial, (c) Athena workgroup billing is unaffected.

## 6. DetectorThresholds

**Shape — flat fields (one per knob), not nested.** Nested-dict-per-detector trades 4 dot-lookups for 4 dict-lookups and adds key-typo risk. Flat keeps each detector reading `thresholds.sms_abuse_rate_per_minute` directly.

```python
@dataclass(frozen=True)
class DetectorThresholds:
    sms_abuse_rate_per_minute: float = 5.0
    auth_burst_count: int = 10
    auth_burst_window_seconds: int = 60
    endpoint_error_rate: float = 0.10
    endpoint_error_min_samples: int = 20
    cost_runaway_delta_usd: float = 10.0
    cost_runaway_window_seconds: int = 300
```

(Defaults TBD-confirmed against current hardcoded constants during Phase A — TDD step's first task is to read each detector and copy its current literals here so behavior is unchanged at the default.)

**Override file** at `OPSMITRA_THRESHOLDS_PATH` (default `None` → defaults only):

```json
{
  "default": { "sms_abuse_rate_per_minute": 6.0 },
  "tenants": {
    "tenant_acme": { "auth_burst_count": 25 }
  },
  "endpoints": {
    "/sms/send": { "sms_abuse_rate_per_minute": 4.0 }
  }
}
```

All keys partial-override; absent keys fall through. Unknown field names raise `ValueError("unknown threshold field: <name>")` at load time (mirrors the lessons.md "fail loud on malformed config" rule).

**Resolver:**

```python
def resolve_thresholds(
    overrides: ThresholdsOverrides,
    tenant: str | None,
    endpoint: str | None,
) -> DetectorThresholds:
    # Precedence: endpoint > tenant > default. Field-by-field merge.
```

**Detector signature change (chosen: pass full `DetectorThresholds`):**

```python
# Before:
def detect_sms_abuse(events: list[Event]) -> list[Anomaly]: ...
# After:
def detect_sms_abuse(events: list[Event], thresholds: DetectorThresholds = DetectorThresholds()) -> list[Anomaly]: ...
```

Default arg keeps existing call sites green during Phase A; orchestration code (Step 8) will resolve per-event-group and pass explicitly.

## 7. Config Env Vars

| Var | Default | Required when | Notes |
|---|---|---|---|
| `OPSMITRA_EVENT_SOURCE` | `local` | always | `local` \| `athena` |
| `OPSMITRA_EVENT_SINK` | `local` | always | `local` \| `s3` |
| `OPSMITRA_LOG_PATH` | `./events.jsonl` | source=local or sink=local | |
| `OPSMITRA_AWS_REGION` | `us-east-1` | source=athena or sink=s3 | |
| `OPSMITRA_S3_BUCKET` | `None` | sink=s3 | raise `ValueError` at sink construction, not at config load |
| `OPSMITRA_S3_PREFIX` | `opsmitra-events` | sink=s3 | |
| `OPSMITRA_ATHENA_DATABASE` | `opsmitra` | source=athena | |
| `OPSMITRA_ATHENA_TABLE` | `events` | source=athena | |
| `OPSMITRA_ATHENA_WORKGROUP` | `primary` | source=athena | |
| `OPSMITRA_ATHENA_OUTPUT_LOCATION` | `None` | source=athena | s3:// URI; raise `ValueError` at source construction |
| `OPSMITRA_THRESHOLDS_PATH` | `None` | never | absent → defaults only |

Use-time validation (not load-time) for bucket / output_location keeps local tests free of AWS env juggling — matches AC #3.

## 8. Test Plan

| # | Test | Phase | Key assertion |
|---|------|-------|---------------|
| 1 | `test_local_source_roundtrip` | B | write 5 events via sink → read back via source → equal |
| 2 | `test_local_source_empty_file` | B | reading nonexistent path returns `[]` |
| 3 | `test_local_sink_appends_ndjson` | B | each line is valid JSON; trailing newline |
| 4 | `test_local_sink_write_result` | B | `WriteResult.written == len(events)`; partitions has 1 entry |
| 5 | `test_query_one_hour_window` | C | SQL contains exactly one `(year=... AND month=... AND day=... AND hour=...)` group |
| 6 | `test_query_two_hour_window` | C | SQL contains two hour predicates joined by `OR` |
| 7 | `test_query_multiday_window` | C | SQL contains `day=` predicates but no `hour=` predicates; comment present |
| 8 | `test_query_tenant_filter_present` | C | `tenant = 'tenant_acme'` AND `tenant_id = 'tenant_acme'` both present |
| 9 | `test_query_no_tenant_filter` | C | no `tenant =` clause |
| 10 | `test_query_type_filter` | C | `endpoint IN ('/sms/send')` present |
| 11 | `test_query_rejects_injection` | C | `build_event_query(..., tenant="x'; DROP TABLE")` raises `ValueError` |
| 12 | `test_athena_source_returns_events_via_stubber` | D | Stubber canned `StartQueryExecution` + `GetQueryExecution` (SUCCEEDED) + `GetQueryResults` (2 rows) → 2 `Event` objects |
| 13 | `test_athena_source_raises_on_failed_state` | D | Stubber `GetQueryExecution` returns FAILED → raises `RuntimeError` with state |
| 14 | `test_s3_sink_put_object_success` | E | Stubber accepts `put_object`; `WriteResult.written == n`; partitions formatted correctly |
| 15 | `test_s3_sink_missing_bucket_raises` | E | constructing sink without bucket raises `ValueError` (use-time) |
| 16 | `test_resolve_thresholds_endpoint_wins` | A | endpoint override beats tenant override |
| 17 | `test_resolve_thresholds_tenant_wins_when_no_endpoint` | A | tenant override applied; default fields preserved |
| 18 | `test_resolve_thresholds_falls_back_to_default` | A | no tenant/endpoint → pure default |
| 19 | `test_detect_sms_abuse_unchanged_with_default_thresholds` | A | existing sms_abuse test rewritten to pass `DetectorThresholds()` — same anomalies emitted |
| 20 | `test_event_source_does_not_import_boto3` | F | `import opsmitra.event_source` then assert `"boto3" not in sys.modules` |

20 tests total (≥15 required). All AWS tests use `botocore.stub.Stubber`; no `moto`, no live boto.

## 9. Phased Order

- **Phase A — Thresholds, behavior-preserving.** Add `DetectorThresholds` + resolver to `config.py`. Refactor 4 detectors to accept it with default arg. Tests 16–19 + existing detector suite stay green.
- **Phase B — Protocols + local I/O.** Add `event_source.py`. Tests 1–4.
- **Phase C — Pure query builder.** Add `aws/__init__.py` + `aws/query_builder.py`. Tests 5–11. No boto3 needed yet.
- **Phase D — Athena source.** Add `aws/athena_source.py`. Tests 12–13. boto3 added to dependencies (extras: `aws`).
- **Phase E — S3 sink.** Add `aws/s3_sink.py`. Tests 14–15.
- **Phase F — Isolation guard + final verify.** Add test 20. Run full `pytest --cov=src --cov-report=term-missing`. Confirm ≥80% on new modules.

## 10. Risks / Open Questions (auto-resolved)

| Decision | Pick | Rationale |
|---|---|---|
| boto3 vs aioboto3 | **boto3** (sync) | Matches `urllib` pattern in `summarizer`; Stubber works out of the box; no event loop wiring for a batch CLI |
| moto vs Stubber | **Stubber** | No new heavy dep; canned responses are explicit and self-documenting |
| Storage format | **JSON + gzip** | Aligns with existing `to_dict()` shape; Parquet conversion is a future scan-cost optimization |
| Multi-day partitions | **Enumerate (tenant, year, month, day) triples, drop hour predicate** for windows > 24h | Avoids exploding OR-clause count; rows still bounded by `timestamp` predicate inside boundary days |
| SQL injection defense | **Whitelist regex on tenant + enum filter on types** | Inputs are tightly bounded; keeps `build_event_query` a pure function for testing |
| Detector signature | **Pass full `DetectorThresholds`** | Simpler wiring than per-detector slice; each detector reads only its own fields |

No question genuinely needs user input.

## 11. Out of Scope (deferred)

- Glue Crawler / Catalog automation (manual DDL run for v0).
- Athena `GetQueryResults` pagination beyond 1000 rows (single-page assumption with TODO).
- S3 lifecycle policies / Intelligent-Tiering.
- Parquet / partition projection.
- aioboto3 / async I/O.
- Per-detector threshold slices (current design passes full object).
- Glue partition registration on S3 write (will need MSCK REPAIR or partition projection — out of scope).

## 12. Ready-for-TDD Checklist

- [x] Acceptance criteria mapped to concrete subgoals
- [x] File layout enumerated (new + modified)
- [x] Public Protocol surfaces frozen
- [x] Athena DDL + S3 prefix layout written
- [x] Query builder contract specified with safety model
- [x] Threshold shape, override file format, resolver precedence specified
- [x] Env var matrix with defaults + when-required notes
- [x] Test plan with ≥15 tests mapped to phases
- [x] Phased order chosen with green-tests guarantee at each phase
- [x] Open questions resolved with rationale
- [ ] User confirmation

---

**WAITING FOR CONFIRMATION**: Proceed to Step 2 (TESTS)? (yes / no / modify)
