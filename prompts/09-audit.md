# Prompt 09 — Audit

Read `AGENTS.md` and `docs/11-observability.md`.

Implement structured security audit events for Run/VM lifecycle, gate decisions, mounts, merge actions, credentials, and runner health.

Correlate events with stable IDs. Never log secrets.

Acceptance: a complete Run can be reconstructed from audit events without exposing credentials.
