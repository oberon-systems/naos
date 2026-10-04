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
| `tools/list` | the tools the gates grant and some rule could allow |
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
else. Every tool belongs to a server, the name a [rule](#rules) uses:

| Tool | Server | Listed when | Gate |
|---|---|---|---|
| `read_file`, `list_dir`, `grep`, `git_status`, `git_diff` | `shell` | the shell policy grants that capability | [07](07-shell-gate.md) |
| `http_request` | `network` | the network policy has an allow rule | [06](06-network-gate.md) |
| `<server>__<tool>` | `<server>` | the server lists it | [External servers](#external-servers) |

A tool is listed only when some rule could allow a call of it as well. A Run
without an `mcp` policy has no rules, so it sees an empty list and every call
is denied, whatever its shell and network policies grant. Listing is a
convenience, not the check: a call the rules allow still reaches its gate,
which may deny it and writes its own audit event.

## Rules

An `mcp` policy is a list of rules, and the broker checks every call against
them before it reaches a gate or leaves the host:

```json
{
  "rules": [
    {"server": "alpha", "tool": "*", "effect": "allow", "max_calls": 500},
    {"server": "alpha", "tool": "delete", "effect": "deny"},
    {"server": "alpha", "tool": "search", "effect": "deny",
     "arguments": {"scope": {"equals": "admin"}}},
    {"server": "alpha", "resource": "docs://alpha/", "effect": "allow"},
    {"server": "shell", "tool": "read_file", "effect": "allow",
     "arguments": {"path": {"prefix": "/naos/alpha/"}}},
    {"server": "network", "tool": "http_request", "effect": "allow",
     "arguments": {"method": {"schema": {"enum": ["GET", "HEAD"]}},
                   "url": {"regex": "https://example\\.com/.*"}}}
  ]
}
```

| Field | Rule |
|---|---|
| `server` | a registered external server, or `shell`, `network` or `secrets` |
| `tool` | an exact tool name, or `*` for every tool of the server |
| `resource` | a URI prefix instead of `tool`; external servers only |
| `effect` | `allow` or `deny` |
| `arguments` | a constraint per argument name, at most 16; tool rules only |
| `max_calls_per_minute` | 1 to 600, allow rules of a tool only |
| `max_calls` | 1 to 1000000 for the whole Run, allow rules of a tool only |

A rule matches a call when the server and the tool match and every
constraint holds. An argument the call does not carry never satisfies a
constraint. A constraint is exactly one of:

| Constraint | Holds when |
|---|---|
| `equals` | the argument is this JSON value |
| `prefix` | the argument is a string that starts with it |
| `regex` | the argument is a string the pattern matches as a whole |
| `schema` | the argument fits the schema fragment |

The schema fragment is a subset of JSON Schema: `type`, `enum`, `const`,
`minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `items`,
`properties`, `required` and `additionalProperties`. A `pattern` matches
anywhere in the string, as in JSON Schema. Patterns run in linear time, so
lookaround, backreferences, conditionals, atomic groups and possessive
quantifiers are refused.

The decision is the same for built-in and external servers:

1. A matching `deny` rule wins over any `allow`.
2. Otherwise the first matching `allow` rule with budget left is charged and
   the call goes on to its gate or server.
3. Otherwise the call is denied: no rule allows it, or the budget of every
   rule that does is spent.

A whole server is one rule with `tool` `*`, and narrower rules sit beside it.
A denied call spends no budget. Budgets live with the Run, not with the
session, and the per-minute budget of the registry entry still applies to an
external server on top of them.

The API stores the rules in a fixed order with every default filled in and
duplicates dropped, so equivalent documents share one digest. A rule is
named by its index in that stored list, in the tool error and in the audit.
`POST /policies` answers 422 for:

- a server the registry does not hold, or a rule naming both a tool and a
  resource, or neither;
- an unknown tool or argument of a built-in server, a resource rule on one,
  or a constraint its argument can never satisfy, such as a prefix on an
  object;
- any rule on `secrets`, which has no tools yet;
- an invalid pattern or a schema keyword outside the subset;
- a budget on a `deny` rule or on a resource rule;
- an `allow` rule that an unconstrained `deny` of the same server always
  covers;
- allow prefixes of two servers that overlap.

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
| a rule denies, no rule allows, or a rule budget is spent | result with `isError: true` and `denied by rule N`, `no rule allows this call` or `budget of rule N is spent` |
| the server budget spent | result with `isError: true` |
| a missing or expired credential, or the server answers 401 or 403 | result with `isError: true` |
| the server answers another error status, a JSON-RPC error or nothing usable | result with `isError: true` |

A gate reason never carries a host path or a subprocess's output
([07](07-shell-gate.md)). The broker timeout sits above the limits of the gates
themselves and bounds what they do not, such as a DNS lookup that never
returns. Gate budgets live with the Run, so a new session does not reset them.

## External servers

External servers are registered once, in the registry of the API
([03](03-api-design.md#mcp-servers)). The rules of an `mcp` policy name them.

When a Run is created the API copies the registry entry of each server an
`allow` rule names and puts it beside the rules. The broker reads this
document, and it stays as it is for the Run even when the registry changes
later:

```json
{
  "servers": [
    {
      "name": "alpha",
      "url": "https://mcp.example.com/mcp",
      "credential": "alpha-token",
      "timeout_seconds": 30,
      "max_calls_per_minute": 60
    }
  ],
  "rules": [
    {"server": "alpha", "tool": "search", "effect": "allow",
     "arguments": {}, "max_calls_per_minute": null, "max_calls": null}
  ]
}
```

| Field | Set by | Rule |
|---|---|---|
| `name` | registry | `^[a-z0-9][a-z0-9-]{0,31}$`, unique, not `shell`, `network` or `secrets`; it prefixes the tool names |
| `url` | registry | https, a hostname, no userinfo, query or fragment |
| `credential` | registry | the name of a secret, or `null` |
| `timeout_seconds` | registry | 1 to 45, default 30 |
| `max_calls_per_minute` | registry | 1 to 600, default 60, for `tools/call` and `resources/read` |

A policy names only registered servers, and a Run is refused while one of
them is unknown or disabled. Disabling a server takes its entry out of the
document of every Run that has not started and leaves the rules, so a rule
index means the same in the Run as in its policy. The broker of such a Run
does not know the name and denies the call before anything leaves the host.

An allow prefix of one server must not be a prefix of another server's, so a
URI routes to at most one server.

The broker speaks Streamable HTTP only. Each server gets its own network gate
built from its URL, which allows https to that host and nothing else
([06](06-network-gate.md)). The session opens on first use with `initialize`
and `notifications/initialized`, carries `Mcp-Session-Id` and
`MCP-Protocol-Version` afterwards, and opens again once when the server
answers 404. A JSON or `text/event-stream` answer is accepted.

`tools/list` asks every server an allow rule names a tool of, keeps the tools
some rule could allow and renames them `<server>__<tool>`. A server that
fails or times out is left out of the list. A call checks the rules and the
budgets before anything leaves the host.

## Credentials

The agent never holds a provider credential:

- a credential belongs to the registry entry and never to a policy: it is
  naos's access to that server, and no tool lists or returns it;
- the API issues credentials to the runner in the desired state, never in the
  policy document, its digest or a log ([03](03-api-design.md));
- each reconcile replaces the credentials of the Run's MCP gate, and one past
  its `expires_at` denies;
- the broker adds `Authorization: Bearer` itself, and the agent cannot set a
  header on an MCP call;
- every string an upstream answer or error carries back has the credential
  value replaced by `<redacted>`.

## Resources

`resources/list` asks every server with an allow prefix and keeps the
resources whose URI starts with one of them and with no deny prefix of that
server. `resources/read` takes `uri`, routes it to the server whose allow
prefix matches and returns its answer unchanged.

| Outcome | Answer |
|---|---|
| no `uri` | error `-32602` |
| no allow prefix matches, or a deny prefix does | error `-32002` |
| budget, credential, server failure or timeout | error `-32603` with the reason |

## Audit

The broker writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `mcp_policy_configured` | `run_id` |
| `mcp_attached` | `run_id` |
| `mcp_credentials_updated` | `run_id`, `names` |
| `mcp_rejected` | `run_id`, `reason` |
| `mcp_call` | `run_id`, `server`, `tool`, `resource`, `decision`, `duration_ms`, `category`, `rule` |

`mcp_credentials_updated` fires when the set of credential names the gate
holds changes, and `names` lists them comma-separated; values are never
logged.

`server` is `shell` or `network` for the built-in tools and the registry name
of an external server, or `unknown`. `tool` is the tool name, `tools/list`,
`resources/list` or `resources/read`, and `unknown` for a name no rule could
allow. `resource` is the policy prefix a read matched, or `none`. `decision`
is `allow` or `deny`, and `category` is `none`, `invalid`, `denied`,
`timeout`, `credential` or `provider`. `rule` is the index of the rule that
decided: the allow rule that was charged, the deny rule that matched or the
allow rule whose budget is spent, and `none` when no rule matched or the
call was refused before the rules. Arguments and URIs are not logged, since they may
carry a secret; the resource a built-in call touched is in the gate's own
event, `shell_*` with the guest path or `network_*` with the host.

## Acceptance

The broker tests in `packages/runner/src/libs/mcp/tests.rs` verify that:

- only tools a gate grants and a rule could allow are listed, and a Run
  without rules lists and calls nothing;
- allow and deny rules, a whole-server rule beside a narrower deny, each
  kind of argument constraint and per-rule budgets hold for built-in and
  external tools, deny wins, and a rule never lifts what a gate refuses;
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
- resources are filtered and read by allow prefix, and a deny prefix is
  neither listed nor read;
- a server error, an unreachable server, a slow server, a spent budget and an
  expired session are handled.

The rule matching itself is tested in
`packages/runner/src/libs/mcp/rules/tests.rs`.

The API tests in `packages/api/tests/test_mcp.py`, `test_mcp_servers.py`,
`test_routes.py` and `test_runner_lifecycle.py` cover the rules, the
registry, secrets and credential issuance: equivalent documents share one
digest, a rule that is malformed or can never match is refused, a Run keeps
the entry it was created with, an unknown or disabled server starts no Run,
and a disable reaches PENDING Runs only.

The smoke test is the end-to-end pass: the guest runs `naos-mcp` over the gate
port and sends `tools/list`, an allowed and a refused call of each built-in
gate, a call of the registered external server's tool, one allowed and one
denied call per rule kind (a whole server, an exact tool, equality, a prefix,
a regular expression, a schema fragment and a budget) and a request reusing
an id. It fails unless the built-in tools are listed, the workspace file comes back, the
reused id is refused, and the runner logged an `mcp_call` for every one of
those decisions, the external server included. Nothing answers as that server,
so its call proves the routing and the failure path, not a working upstream.

```bash
make test
make smoke
```
