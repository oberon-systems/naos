# Prompt 06 — MCP Gate

Read `AGENTS.md` and `docs/08-mcp-gate.md`.

Implement a per-Run MCP broker with server/tool/method/resource policy, credential references, timeouts, and audit.

The agent must not receive unrestricted provider credentials. Unknown capabilities and provider/gate failures fail closed.

Acceptance: allowed calls succeed; unauthorized calls, expired credentials, duplicate abuse, timeouts, and provider failures are handled safely.
