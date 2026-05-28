# OpsMitra

OpsMitra is a private AI-assisted log anomaly detector for learning AWS, local model integration, alerting, and AI-native backend design.

The first version is intentionally local-first:

- generate synthetic SaaS logs
- detect anomalies with deterministic rules
- summarize structured evidence with a local/private model
- send Slack-ready alerts
- add AWS S3/Athena integration after the local loop works

See the build plan at [`.Codex/plans/opsmitra-build-plan.md`](.Codex/plans/opsmitra-build-plan.md).
