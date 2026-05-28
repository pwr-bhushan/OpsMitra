# Mitra Lessons

## Domain Knowledge

- Mitra is a learning project, not a startup validation exercise.
- Detection should be deterministic; the local/private model explains and summarizes scoped anomaly evidence.
- v0 starts with synthetic logs before touching production-like AWS data.

## Coding Lessons

- Always do Mitra project changes on the `dev` branch, not `main`.
- Keep AWS-dependent code behind interfaces so local tests do not require AWS credentials.
- Do not send full raw logs to model prompts; pass scoped anomaly evidence only.
