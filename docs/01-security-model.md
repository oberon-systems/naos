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

A security policy is immutable during a Run. Changing the security boundary requires a new Run.

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

A model provider's key never enters the VM. The model gateway
([13](13-model-gateway.md)) drops every auth header the agent sends, sets the
provider's own on the host and redacts the key in every answer, so the agent
only ever holds a placeholder. The runner build with the `smoke-stubs` feature
lets the gateway reach loopback and trust an extra CA; it exists for `make
smoke` and is never a release.

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
