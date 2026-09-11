# Prompt 04 — Network Gate

Read `AGENTS.md`, `docs/01-security-model.md`, `docs/02-threat-model.md`, and `docs/06-network-gate.md`.

Implement per-Run default-deny network enforcement with protocol/hostname/IP rules and deny precedence.

Protect against localhost/private ranges, IPv4/IPv6 bypass, direct IP, DNS rebinding, redirects, CONNECT, and proxy bypass.

If policy evaluation fails, deny.

Acceptance: integration tests prove allowed destinations work and forbidden destinations cannot be reached.
