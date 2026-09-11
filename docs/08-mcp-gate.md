# 08 — MCP Gate

## Prompt

Implement MCP access as a per-Run broker.

Architecture:
Agent -> MCP Gate -> External MCP/Service

Policy controls:

- allowed MCP servers;
- tools/methods;
- resources;
- credential reference;
- timeout;
- usage limits.

Unknown server/method/resource -> DENY.
Credential unavailable -> DENY.
Gate/provider failure -> DENY.

Keep credentials outside the VM where possible.

Audit server, method/tool, safe resource identifier, decision, duration, and error category. Never log secrets.

Test allowed and unauthorized calls, credential expiry/leakage, duplicates, timeout, and provider failure.
