# 01 — Security Model

## Prompt

Treat the agent as malicious and Naos infrastructure as the enforcement boundary.

## Trust model

Trusted: API/control plane, approved runner, approved image, gate infrastructure, appropriately hardened host/QEMU.

Untrusted: agent process, generated files, network responses, MCP responses.

## Capability model

Explicit capabilities:

- filesystem mount;
- network destination/protocol;
- shell operation;
- MCP server/method/resource;
- model provider/model and token budget;
- credentials.

Default deny. Deny overrides allow.

Nothing changes a security policy during a Run except an operator. An operator may
replace the policy of a gate on a STARTED Run: the change is audited, the Run keeps
every policy it held, and a call in flight ends under the policy it started with
([03](03-api-design.md#changing-a-policy-of-a-started-run)). The mount policy never
changes, because the mounts are fixed when the VM starts.

## Failure behavior

If policy cannot be evaluated, deny.
If credentials cannot be validated, deny.
If a gate is unavailable, deny the protected operation.

## Credentials

Separate IDs from credentials. Prefer scoped short-lived runner, Run, gate, and console tokens. Never expose long-lived infrastructure credentials to the agent.

A secret value is write-only. The operator types it once, and no API
response, error or audit event carries it afterwards. A rotation reaches the
next issue to a runner; a Run already holding the old value keeps it until its
credential TTL ends. A secret cannot be deleted while a policy names it or an
open Run holds it.

An agent gets a secret only by asking the MCP broker for it, and only one an
`mcp` policy grants by its exact name ([08](08-mcp-gate.md#secrets)). The
runner holds granted values in memory for that Run alone. Once the agent has
read a value it is the agent's: it may reach the model provider, the agent's
files, the upper disk and the console log. naos itself writes no value to the
VM disk, the console log, the audit or the runner's state directory, and
every read is audited by name.

A model provider's key never enters the VM. The model gateway
([13](13-model-gateway.md)) drops every auth header the agent sends, sets the
provider's own on the host and redacts the key in every answer, so the agent
only ever holds a placeholder.

## Host risks

What a feature costs the host, and how it is kept small. A feature adds its
line here when it lands.

| Risk | Where it comes from | How it is limited |
|---|---|---|
| A guest reaches any vsock service of the host | A Run with a model policy has a vsock device, and vsock lets the guest connect to every listener at the host's CID 2, not only the gateway | The runner host runs no other vsock service; [host/vhost-vsock.md](host/vhost-vsock.md) checks it with `ss --vsock -l`, and `scripts/vhost-vsock-install.sh check` fails when one listens. Only a Run with a model policy gets the device |
| A process of the runner's user takes a guest CID | Read and write on `/dev/vhost-vsock` lets any process of that user register a free CID. It cannot take the CID of a running VM, but it can take one a stopped VM of another hypervisor used, and receive the host's connections to it, or hold CIDs so VMs fail to start | The ACL covers `/dev/vhost-vsock` only and only the runner's user, never the `kvm` group. In production the runner runs as a user of its own. The runner picks a random CID and serves a connection only from the CID of its VM, so a host process at CID 1 or another VM is refused |
| A larger kernel surface for the runner's user | The vhost ioctls of `/dev/vhost-vsock` become reachable | Small next to `/dev/kvm`, which the runner already needs |
| A Run spends past its token budget | Calls already in flight when the budget runs out still finish, and the budget lives in the runner's memory, so a restarted runner starts it afresh | Bounded by the calls in flight and by the Run's rate limit per provider |
| A granted secret leaves naos's control | `secrets__get` hands the value to the agent, which may send it to the model provider, write it to its files and so the upper disk, or print it into the console log | Granted by exact name only, never by pattern; a per-rule budget; every read audited by name; issued with the credential TTL and only to the runner holding the Run. Registry and model provider credentials are never in the granted set |
| A test build weakens the gateway | The `smoke-stubs` feature lets the gateway reach loopback and trust the CA in `NAOS_AGENT_SMOKE_CA_FILE` | Off by default and only built by `make smoke`; no release enables it |

## Security acceptance

Prove an untrusted agent cannot:

- read unauthorized host files;
- escape mounts;
- access localhost/private networks;
- bypass network policy via direct IP or DNS rebinding;
- execute unauthorized host commands;
- access another Run/VM;
- call unauthorized MCP methods;
- reach a model outside its policy or past its token budget;
- obtain infrastructure credentials or a model provider's key.

Prompt instructions are never enforcement.
