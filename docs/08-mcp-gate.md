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

The agent reaches the broker over one channel, which the boot probe exercises
on every start:

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
| `tools/list` | the tools the Run was granted |
| `tools/call` | a tool result, see [Calls](#calls) |
| `ping` | an empty result |
| a JSON-RPC 2.0 message without an `id` | nothing |
| a request `id` already used in this session | error `-32600` |
| any other method | error `-32601` |
| a line that is not JSON | error `-32700` |
| JSON that is not a JSON-RPC 2.0 request | error `-32600` |

A line longer than 1 MiB ends the session, because the guest is untrusted and
an unbounded line would buffer without limit. So does a session that sends more
than 65536 requests, because every request id is remembered. The runner
reconnects on its next reconcile pass, as it does after a restart: QEMU keeps
the socket listening for as long as the VM runs.

## Tools

The broker serves the gates the runtime registered for the Run and nothing
else. `tools/list` names exactly what the Run was granted:

| Tool | Listed when | Gate |
|---|---|---|
| `read_file`, `list_dir`, `grep`, `git_status`, `git_diff` | the shell policy grants that capability | [07](07-shell-gate.md) |
| `http_request` | the network policy has an allow rule | [06](06-network-gate.md) |

A Run without shell or network policy sees an empty list. Listing is a
convenience, not the check: a call to a known tool that was not granted still
reaches its gate, which denies it and writes its own audit event.

## Calls

`tools/call` parses the arguments strictly, builds one gate request and waits
for it. Unknown argument fields are refused, and so is a missing or mistyped
one.

| Tool | Arguments | Text of a successful result |
|---|---|---|
| `read_file` | `path` | the file, which must be UTF-8 |
| `list_dir` | `path` | a JSON array of `name`, `kind`, `size` |
| `grep` | `path`, `pattern` | a JSON array of `path`, `line`, `text` |
| `git_status`, `git_diff` | `path` | the git output |
| `http_request` | `method`, `url`, optional `headers` and `body` | a JSON object of `status`, `headers`, `body`; the body must be UTF-8 |

`http_request` accepts `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE` and
`OPTIONS`. It refuses the `Host`, `Connection`, `Transfer-Encoding`,
`Content-Length` and any `Proxy-*` header. The gate pins the connection to the
addresses it authorized for the URL host, so a `Host` header would otherwise
reach a different virtual host behind the same address.

## Failure behavior

Every failure denies, and the agent can tell a malformed call from a refused
one:

| Outcome | Answer |
|---|---|
| unknown tool, invalid arguments, refused method or header | error `-32602` |
| the gate denies or fails | result with `isError: true` and the gate's reason |
| the call runs longer than 45 seconds | result with `isError: true`, `call timed out` |
| a file or response body that is not UTF-8 | result with `isError: true` |

A gate reason never carries a host path or a subprocess's output
([07](07-shell-gate.md)). The broker timeout sits above the limits of the gates
themselves and bounds what they do not, such as a DNS lookup that never
returns. Gate budgets live with the Run, so a new session does not reset them.

## Audit

The broker writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `mcp_attached` | `run_id` |
| `mcp_rejected` | `run_id`, `reason` |
| `mcp_call` | `run_id`, `server`, `tool`, `decision`, `duration_ms`, `category` |

`server` is `naos`. `tool` is `unknown` for a name the broker does not serve.
`decision` is `allow` or `deny`, and `category` is `none`, `invalid`, `denied`
or `timeout`. Arguments are not logged, since a header or body may carry a
secret; the resource a call touched is in the gate's own event, `shell_*` with
the guest path or `network_*` with the host.

## Not built yet

A Run carrying an MCP policy is still refused. Proxying external MCP servers
comes next:

- `mcppol` names the servers, their allowed tools and resources, a credential
  reference, a timeout and usage limits;
- the API issues the credential to the runner with the policy snapshot, and it
  never enters the policy document, its digest, the VM or a log;
- a missing or expired credential denies;
- a server is reached over Streamable HTTP only, through a network gate built
  from its URL, and its tools are listed as `<server>__<tool>`.

## Acceptance

The broker tests in `packages/runner/src/libs/mcp/tests.rs` verify that:

- only granted tools are listed;
- granted shell tools and an allowed request answer;
- a capability that was not granted, a path outside every mount, traversal, a
  binary file and a request without network policy are tool errors;
- unknown tools and malformed arguments are `-32602`;
- a denied host, a `Host` or `Proxy-*` header, `Transfer-Encoding` and
  `CONNECT` never reach the destination;
- a reused request id is refused;
- a slow destination times out and an unreachable one is a tool error.

```bash
make test
make smoke
```
