<!-- TEMPLATE — fill in after a replay run -->

# AWS Window Replay Findings

Fill in this template after completing the AWS window replay exercise described
in `docs/aws-window-replay.md`. Commit your completed findings to preserve a
record of what you observed and what you changed.

---

## Run Date

`YYYY-MM-DD`

---

## Window

`<ISO start> → <ISO end>`

**Source type:** *(athena / local)*
**Tenant filter:** *(or "all tenants")*

---

## Detectors Triggered

| Detector | Count | Sample Fingerprints |
|----------|-------|---------------------|
| `sms_abuse` | — | — |
| `auth_burst` | — | — |
| `endpoint_error_spike` | — | — |
| `cost_runaway` | — | — |

---

## False Positives

Anomalies that fired but do not represent real incidents.

| Detector | Tenant | Subject | Reason It Was a False Positive |
|----------|--------|---------|-------------------------------|
| — | — | — | — |

**Root cause analysis:** *(Were thresholds too tight? Batch job causing a burst?)*

---

## False Negatives (Missed Incidents)

Real incidents that occurred in the window but were NOT detected.

| Incident | Tenant | Subject | Why the Detector Missed It |
|----------|--------|---------|---------------------------|
| — | — | — | — |

---

## Athena Cost

| Metric | Value |
|--------|-------|
| Bytes scanned | — |
| Estimated cost @ $5/TB | — |
| Queries executed | — |

---

## Detection Delay

| Anomaly Fingerprint | `detection_delay_upper_bound_seconds` |
|---------------------|---------------------------------------|
| — | — |

---

## Threshold Adjustments Applied

JSON snippet of any overrides used during or after the replay:

```json
{}
```

---

## Conclusions

**Learnings:**

- *(What did you learn about the detector behaviour?)*

**Suggested threshold changes:**

- *(Detector X: raise/lower threshold from Y to Z because …)*

**New detectors suggested:**

- *(Pattern observed that isn't covered by existing detectors)*

**Follow-ups:**

- *(Data quality issues, schema mismatches, partition gaps, etc.)*
