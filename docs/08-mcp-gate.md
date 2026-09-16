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

## Transport

The broker is not built yet. What exists is the channel it will use, which the
boot probe exercises on every start, and a session that offers no tool at all.

```text
agent -> naos-mcp (stdio) -> /run/naos/mcp -> virtio-serial naos.mcp -> mcp.sock -> runner
```

The guest side has no logic. `naos-mcp` is `socat` between its stdio and the
port, so nothing inside the VM makes a decision or holds a credential; the
runner end is the only place a request is read.

Messages are newline-delimited JSON-RPC 2.0, the MCP stdio framing:

| Message | Answer |
|---|---|
| `initialize` | the requested protocol version when supported, else the latest; `tools` capability; server `naos` |
| `tools/list` | an empty list |
| `ping` | an empty result |
| a JSON-RPC 2.0 message without an `id` | nothing |
| any other method | error `-32601` |
| a line that is not JSON | error `-32700` |
| JSON that is not a JSON-RPC 2.0 request | error `-32600` |

A line longer than 1 MiB ends the session, because the guest is untrusted and
an unbounded line would buffer without limit. The runner reconnects on its next
reconcile pass, as it does after a restart: QEMU keeps the socket listening for
as long as the VM runs.

The session writes `mcp_attached` when it connects and `mcp_rejected` with a
reason for every protocol violation. Per-call audit belongs to the broker.
