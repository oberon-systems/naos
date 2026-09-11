# 11 — Observability

## Prompt

Implement security-oriented audit events.

Record:

- Run lifecycle;
- VM lifecycle;
- gate decisions;
- mounts;
- merge actions;
- credentials;
- runner health.

Correlate with run_id, vm_id, runner_id, and gate_id where applicable.

Never log tokens, passwords, API keys, private credentials, or sensitive raw payloads.

Eventually expose metrics for active Runs, startup time, duration, gate requests/denials, overlay size, resource use, runner health, and queue latency.
