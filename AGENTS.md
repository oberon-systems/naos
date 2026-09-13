# AGENTS.md

## Mission

Naos is a security-focused execution environment for running untrusted AI agents inside isolated VMs.

Primary invariant:

> An untrusted agent must not cross a security boundary unless the capability was explicitly granted.

Treat Naos as a security boundary, not merely as a VM launcher.

## Mandatory principles

1. Default deny.
2. Deny overrides allow.
3. Security policy is immutable for the lifetime of a Run.
4. Host filesystem, network, shell, and MCP access are explicit capabilities.
5. Security failures fail closed.
6. Secrets remain outside the VM whenever possible.
7. The queue is transport, never the source of truth.
8. The runner executes policy; it does not invent permissions.
9. Merge cannot escape the authorized workspace.
10. Prompt instructions are not a security boundary.
11. Every security-sensitive feature requires negative tests.

## Run model

Run is the primary runtime entity. VM is an implementation detail.

Use distinct identifiers where applicable:

- agent_id
- run_id
- vm_id
- runner_id
- lease_id
- policy_id

Expected lifecycle:
PENDING -> STARTING -> STARTED -> STOPPING -> COLLECTING -> WAITING_MERGE -> COMPLETED

Failures may transition to FAILED. Stopping a PENDING Run moves it to
CANCELLED. COMPLETED, FAILED and CANCELLED are terminal.

## Architecture

Web -> API/Control Plane -> queue/events -> Naos Runner -> QEMU/Gates/Console -> Agent VM.

The API owns desired and persistent state. The runner reconciles actual state with desired state.

## Runner

The runner must register, maintain a lease, receive Runs, create/destroy VMs, manage overlays/mounts/gates/console, collect diffs, report lifecycle, and reconcile after restart.

It must not broaden permissions, bypass gates, or invent mounts.

## Security

Treat the agent, agent-generated files, network responses, and MCP responses as untrusted.

Prefer capability APIs over ambient authority.

Never implement privileged host execution as `/bin/sh -c <agent input>`.

Never use UUIDs as credentials. Use scoped, short-lived credentials.

Never log secrets.

## Testing

For every security boundary test both positive and negative paths. Consider path traversal, symlink/hardlink escapes, direct-IP bypass, IPv4/IPv6, localhost/private networks, DNS rebinding, redirects/CONNECT, command injection, duplicate queue messages, expired credentials, component failure, and resource exhaustion.

## Development behavior

Before coding:

1. read the relevant design docs;
2. identify the trust boundary;
3. define the enforcement point;
4. define failure behavior;
5. define security tests.

Implement the smallest scoped change. Do not silently redesign the architecture.

If a change weakens a mandatory invariant, stop and propose an ADR.

Security wins over backward compatibility.
