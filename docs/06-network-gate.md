# 06 — Network Gate

## Prompt

Implement per-Run default-deny network enforcement.

Example:

```yaml
allow:
  - protocol: https
    host: google.com
deny:
  - host: private.example.com
```

Deny overrides allow.

Enforce policy at connection establishment. Handle hostname normalization, DNS, IPv4/IPv6, direct IP, loopback, private/link-local ranges, metadata endpoints, DNS rebinding, redirects, CONNECT, proxy bypass, and connection reuse.

Log run_id, timestamp, destination, protocol, decision, matched rule, and safe failure reason. Never log credentials.

If policy cannot be evaluated, deny.

Security tests must include allowed/denied hosts, direct IP, localhost, RFC1918, IPv6 loopback, DNS rebinding, redirects, CONNECT, and malformed hostnames.

## Policy

A network policy is created like any other, and the API resolves it before it
is stored:

```bash
curl -fsS "$api/api/v1/policies" -d '{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}'
```

The document holds an `allow` list and a `deny` list, each of at most 64 rules.
A rule carries any of `protocol` (`http` or `https`), `host` and `ip`, and must
constrain at least one of them: a rule that constrains nothing would turn an
allow list into allow-all. A policy with no rules at all is refused for the same
reason.

Hosts are lowercased and lose a trailing dot, so `EXAMPLE.COM.` and
`example.com` resolve to one document and therefore to one `netpol_` id. An
address literal in `host` is refused - addresses belong in `ip`, which is
matched against what the destination resolves to. So are `localhost` and any
name with an empty, over-long or otherwise invalid label.

A Run points at the policy through `spec.network.policy`, and the runner
receives the resolved document as a snapshot rather than the id. An operator
can change the policy of a started Run ([Live changes](#live-changes)).

## Enforcement point

The VM has no network device at all: the QEMU command line carries `-nic none`
and a unit test fails if `-netdev` ever appears. The single egress is
`NetworkGate::send` on the host, in `packages/runner/src/libs/network/`.

The runner builds the gate in `Runtime::ensure`, before the image is fetched, so
a policy it cannot parse fails the Run instead of starting a VM that would be
enforced by nothing. The gate is then registered against the Run id for as long
as the VM lives and removed when the VM is destroyed. A reconcile pass keeps the
gate it registered and swaps only its rules: rebuilding the gate would hand the
Run a fresh request budget on every tick.

The gate owns the HTTP client and never hands one out. A caller submits a
`GateRequest` and receives a `GateResponse`, so an authorization can never be
reused for a second destination, and no call can escape the limits below. The
per-Run MCP broker ([08](08-mcp-gate.md)) will be the only caller.

## Decision order

`send` spends a request from the budget, authorizes the destination, then issues
the request against the addresses that were authorized. Authorization walks this
order and denies at the first failure:

| Step | Denied when |
|---|---|
| hostname | it does not normalize, or it is `localhost` |
| protocol | it is not `http` or `https` |
| direct address | the destination is an address literal rather than a name |
| resolution | the name resolves to nothing |
| reserved range | any resolved address is reserved (see below) |
| deny list | a deny rule matches |
| allow list | no allow rule matches |

Deny is therefore checked before allow, and an absent rule denies. A rule
matches when every field it sets matches: `protocol` and `host` against the
destination, `ip` against the set of addresses the name resolved to.

Reserved covers loopback, unspecified, multicast, `0.0.0.0/8`, `10.0.0.0/8`,
`100.64.0.0/10`, `169.254.0.0/16` - which is the cloud metadata endpoint -
`172.16.0.0/12`, `192.0.0.0/16`, `192.168.0.0/16`, `198.18.0.0/15` and
`224.0.0.0/4` upwards, and for IPv6 the unspecified and loopback addresses,
multicast, `fc00::/7`, `fe80::/10` and any IPv4-mapped form of the above.

The addresses that passed authorization are pinned into the client, so the
connection goes to what was authorized and a second DNS answer cannot redirect
it. Redirects are not followed: a `302` comes back to the caller as it is, and
its `Location` only reaches the network if the caller submits it through `send`
again and it passes on its own merits.

The gate is a client, not a proxy. It never issues `CONNECT`, environment proxy
settings are ignored, and the VM has no network device, so the agent has no
proxy to reach either way.

## Live changes

An operator changes the `network` policy of a STARTED Run through the API
([03](03-api-design.md#changing-a-policy-of-a-started-run)), and the desired
state then carries the new document. The runner hands it to the Run's gate on
its next reconcile pass:

- The gate swaps the allow and deny lists whole. A request takes both lists
  once, when it is authorized, so the swap falls between two requests and a
  request in flight ends under the policy it started with.
- A host that was added answers the next request, and one that was taken away
  is denied from the next request on.
- The request budget belongs to the Run, not to a rule, so every request
  spent before the swap stays spent.
- After the swap the broker sends `notifications/tools/list_changed` to an
  initialized agent ([08](08-mcp-gate.md#live-changes)), because
  `http_request` is listed only while the policy allows something.
- A document the runner cannot read grants nothing: every request is denied
  until a readable document arrives, and the reconcile pass reports the error.

```bash
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}'
```

## Limits

| Limit | Value |
|---|---|
| request timeout | 30 seconds |
| response body | 8 MiB |
| requests per Run | 120 per 60 second window |

The body cap is enforced on the bytes actually read, not on `Content-Length`,
which the destination controls, and an oversized response yields an error rather
than a truncated body.

## Failure behavior

Every failure denies. An unparseable policy fails the Run before it starts; an
unparseable URL, a DNS failure, a transport failure, an exhausted budget and an
oversized response all fail the single call. Nothing falls back to a permissive
default.

## Audit

The gate writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `network_policy_configured` | `run_id`; at the start of the VM and each time the gate holds a changed policy |
| `network_allowed` | `run_id`, `protocol`, `host`, `rule` |
| `network_denied` | `run_id`, `protocol`, `host`, `rule`, `reason` |

`rule` is `allow[<n>]`, `deny[<n>]` or `none`, so a decision points at the line
of the policy that made it. Host and protocol are logged, never the full URL: a
URL can carry userinfo, which is a credential.

## Acceptance

Acceptance tests must verify:

- allowed destinations work;
- forbidden destinations cannot be reached;
- direct IP, localhost, RFC1918 and IPv6 loopback are denied;
- DNS rebinding, redirects and proxy bypass cannot route around the policy;
- malformed hostnames and malformed policies are refused;
- a host added to a running Run answers its next request and a removed one is
  denied from the next request on;
- a request in flight finishes under the policy it started with.

The gate tests in `packages/runner/src/libs/network/tests.rs` cover all of
these, and `packages/api/tests/test_network.py` covers policy resolution. The
smoke test drives the gate from inside the guest through the MCP broker: one
request to the allowed host and one to a host that resolves but the policy does
not name, and it fails unless `network_allowed` and `network_denied` both reach
the agent log. A destination that does not resolve is refused before the rules
are matched, which is why the refused host is a real one.
The allowed request really leaves the host, so the run needs outbound https.

```bash
make test
make smoke
```
