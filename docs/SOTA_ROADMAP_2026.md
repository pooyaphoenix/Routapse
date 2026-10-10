# SOTA platform hardening (2026 Q4)

GitHub Issues are disabled on this repository; track this epic here until issues are enabled.

## Universal

- CI on every PR (lint, test, compose validate) — see `.github/workflows/ci.yml`
- Dependabot + CodeQL; SBOM in release pipeline
- OpenTelemetry on router + backend; structured JSON logs
- Secrets scanning (`gitleaks`); document env in `.env.example`

## Router / gateway specific

- Load tests (k6) on classify + proxy paths; published SLOs
- Contract tests for OpenAI-compatible surface
- Multi-tenant isolation tests for store paths
- Shadow routing: compare Laya/Jev decisions vs baseline model without serving users

## Definition of done

CI green on `main`, reproducible docker compose up, architecture diagram in `docs/`, security section in README.
