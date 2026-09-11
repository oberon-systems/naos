# 12 — Roadmap

## P0 — Security Boundary

QEMU, immutable image, overlay, explicit mounts, Rust runner, API/Run state, registration, lease, start/stop, console, cleanup.

Exit criterion: automated tests prove an untrusted agent cannot cross VM, filesystem, network, or runner boundaries without explicit grants.

## P1 — Network Gate

Default deny, HTTP/HTTPS, hostname/IP rules, deny precedence, DNS safety, logging, timeouts/rate limits, SSRF protections.

## P2 — Shell Gate

Capability APIs, path confinement, optional restricted exec, resource limits, audit.

## P3 — MCP Gate

Per-Run broker, method/resource policy, credential isolation, audit, timeouts.

## P4 — Merge System

Overlay diff, staging, review, selected-file merge, always/ask/never, security tests.

## P5 — Web UI

Runs, details, console, logs, policies, profiles, images, diff viewer, merge workflow.

## P6 — Production Hardening

Signed images, digest verification, scanning, quotas, sandbox hardening, token rotation, stronger runner auth, audit integrity, metrics, backups, migrations, alerting.

Implement in this order. Do not prioritize UI polish over security boundaries.
