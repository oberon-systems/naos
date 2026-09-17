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
| `initialize` | the requested protocol version when supported, else the latest; `tools` capability, and `resources` when the MCP policy allows any; server `naos` |
| `tools/list` | the tools the Run was granted |
| `tools/call` | a tool result, see [Calls](#calls) |
| `resources/list`, `resources/read` | see [Resources](#resources); `-32601` without resources in the policy |
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
| `<server>__<tool>` | the MCP policy allows `tool` on `server` and the server lists it | [External servers](#external-servers) |

A Run without shell, network or MCP policy sees an empty list. Listing is a
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
| unknown tool or server, invalid arguments, refused method or header | error `-32602` |
| the gate denies or fails | result with `isError: true` and the gate's reason |
| the call runs longer than 45 seconds, or the server's `timeout_seconds` | result with `isError: true`, `call timed out` |
| a file or response body that is not UTF-8 | result with `isError: true` |
| a tool the policy does not allow, the server budget spent | result with `isError: true` |
| a missing or expired credential, or the server answers 401 or 403 | result with `isError: true` |
| the server answers another error status, a JSON-RPC error or nothing usable | result with `isError: true` |

A gate reason never carries a host path or a subprocess's output
([07](07-shell-gate.md)). The broker timeout sits above the limits of the gates
themselves and bounds what they do not, such as a DNS lookup that never
returns. Gate budgets live with the Run, so a new session does not reset them.

## External servers

The `mcp` policy names the external MCP servers a Run may reach. The API
resolves it into this document ([03](03-api-design.md)):

```json
{
  "servers": [
    {
      "name": "alpha",
      "url": "https://mcp.example.com/mcp",
      "tools": ["fetch", "search"],
      "resources": ["docs://alpha/"],
      "credential": "alpha-token",
      "timeout_seconds": 30,
      "max_calls_per_minute": 60
    }
  ]
}
```

| Field | Rule |
|---|---|
| `name` | `^[a-z0-9][a-z0-9-]{0,31}$`, unique; it prefixes the tool names |
| `url` | https, a hostname, no userinfo, query or fragment |
| `tools`, `resources` | at least one of them; tool names are exact, resources are URI prefixes |
| `credential` | the name of a secret, or `null` |
| `timeout_seconds` | 1 to 45, default 30 |
| `max_calls_per_minute` | 1 to 600, default 60, for `tools/call` and `resources/read` |

A resource prefix of one server must not be a prefix of another server's, so
a URI routes to at most one server.

The broker speaks Streamable HTTP only. Each server gets its own network gate
built from its URL, which allows https to that host and nothing else
([06](06-network-gate.md)). The session opens on first use with `initialize`
and `notifications/initialized`, carries `Mcp-Session-Id` and
`MCP-Protocol-Version` afterwards, and opens again once when the server
answers 404. A JSON or `text/event-stream` answer is accepted.

`tools/list` asks every server, keeps the tools the policy allows and renames
them `<server>__<tool>`. A server that fails or times out is left out of the
list. A call checks the tool and the budget before anything leaves the host.

## Credentials

The agent never holds a provider credential:

- the API issues credentials to the runner in the desired state, never in the
  policy document, its digest or a log ([03](03-api-design.md));
- each reconcile replaces the credentials of the Run's MCP gate, and one past
  its `expires_at` denies;
- the broker adds `Authorization: Bearer` itself, and the agent cannot set a
  header on an MCP call;
- every string an upstream answer or error carries back has the credential
  value replaced by `<redacted>`.

## Resources

`resources/list` asks every server with resource prefixes and keeps the
resources whose URI starts with one of them. `resources/read` takes `uri`,
routes it to the server whose prefix matches and returns its answer
unchanged.

| Outcome | Answer |
|---|---|
| no `uri` | error `-32602` |
| no server prefix matches | error `-32002` |
| budget, credential, server failure or timeout | error `-32603` with the reason |

## Audit

The broker writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `mcp_policy_configured` | `run_id` |
| `mcp_attached` | `run_id` |
| `mcp_credentials_updated` | `run_id`, `names` |
| `mcp_rejected` | `run_id`, `reason` |
| `mcp_call` | `run_id`, `server`, `tool`, `resource`, `decision`, `duration_ms`, `category` |

`mcp_credentials_updated` fires when the set of credential names the gate
holds changes, and `names` lists them comma-separated; values are never
logged.

`server` is `naos` for the built-in tools and the policy name of an external
server, or `unknown`. `tool` is the tool name, `tools/list`, `resources/list`
or `resources/read`, and `unknown` for a name the policy does not allow.
`resource` is the policy prefix a read matched, or `none`. `decision` is
`allow` or `deny`, and `category` is `none`, `invalid`, `denied`, `timeout`,
`credential` or `provider`. Arguments and URIs are not logged, since they may
carry a secret; the resource a built-in call touched is in the gate's own
event, `shell_*` with the guest path or `network_*` with the host.

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
- a slow destination times out and an unreachable one is a tool error;
- an allowed upstream tool is listed and answers over JSON and event streams,
  with the Bearer credential and the session header;
- a tool or server outside the policy, and a missing or expired credential,
  never reach the server;
- a credential the server echoes is redacted;
- resources are filtered and read by prefix;
- a server error, an unreachable server, a slow server, a spent budget and an
  expired session are handled.

The API tests in `packages/api/tests/test_mcp.py`, `test_routes.py` and
`test_runner_lifecycle.py` cover the policy, secrets and credential issuance.

The smoke test is the end-to-end pass: the guest runs `naos-mcp` over the gate
port and sends `tools/list`, an allowed and a refused call of each built-in
gate, a call of the external server's tool and a request reusing an id. It
fails unless the built-in tools are listed, the workspace file comes back, the
reused id is refused, and the runner logged an `mcp_call` for every one of
those decisions, the external server included. Nothing answers as that server,
so its call proves the routing and the failure path, not a working upstream.

```bash
make test
make smoke
```
