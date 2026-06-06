# OpsMitra Lessons

## Domain Knowledge

- OpsMitra is a learning project, not a startup validation exercise.
- The project name changed from Mitra to OpsMitra because OpsMitra is more descriptive and makes the operations/log-alerting purpose clearer.
- Detection should be deterministic; the local/private model explains and summarizes scoped anomaly evidence.
- v0 starts with synthetic logs before touching production-like AWS data.

## Coding Lessons

- Always do OpsMitra project changes on the `dev` branch, not `main`.
- When the user renames the project, update user-facing docs, plans, trackers, and lessons consistently.
- Keep AWS-dependent code behind interfaces so local tests do not require AWS credentials.
- Do not send full raw logs to model prompts; pass scoped anomaly evidence only.
