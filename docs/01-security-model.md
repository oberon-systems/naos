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
- credentials.

Default deny. Deny overrides allow.

A security policy is immutable during a Run. Changing the security boundary requires a new Run.

## Failure behavior

If policy cannot be evaluated, deny.
If credentials cannot be validated, deny.
If a gate is unavailable, deny the protected operation.

## Credentials

Separate IDs from credentials. Prefer scoped short-lived runner, Run, gate, and console tokens. Never expose long-lived infrastructure credentials to the agent.

## Security acceptance

Prove an untrusted agent cannot:

- read unauthorized host files;
- escape mounts;
- access localhost/private networks;
- bypass network policy via direct IP or DNS rebinding;
- execute unauthorized host commands;
- access another Run/VM;
- call unauthorized MCP methods;
- obtain infrastructure credentials.

Prompt instructions are never enforcement.
