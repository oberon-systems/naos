# 00 — Architecture

## Prompt

Implement Naos around a Run abstraction.

## Stack

- Python 3.12+, FastAPI, Pydantic, SQLModel, SQLite initially.
- HTMX web UI.
- Rust runner.
- QEMU.
- Packer-built immutable images.
- Redis or equivalent for transport/events.

## Architecture

Web -> API/Control Plane -> queue/events -> Naos Runner -> QEMU/Gates/Console -> Agent VM.

Each Run gets:

- immutable base image;
- disposable writable overlay;
- explicit mounts;
- explicit network/shell/MCP capabilities;
- console;
- audit trail.

On completion:

1. stop VM;
2. destroy VM;
3. collect overlay;
4. produce structured diff;
5. apply merge policy.

## Domain model

Agent, AgentImage, RuntimeProfile, SecurityPolicy, MountProfile, Run, Runner, Lease, RunSpec.

## RunSpec

```yaml
run_id: run_01ABC
image:
  id: image_123
  digest: sha256:...
runtime:
  cpu: 4
  memory: 8192
  disk: 30G
mounts:
  - host_path: /data/project
    guest_path: /workspace
    mode: rw
network:
  policy_snapshot: netpol_42
shell:
  policy_snapshot: shellpol_10
mcp:
  policy_snapshot: mcppol_7
merge:
  policy: ask
timeout: 7200
```

Security policy is snapshotted for the Run. Runtime settings may be mutable only where explicitly supported.

## Acceptance criteria

- Run is the primary lifecycle entity.
- VM is disposable.
- Security configuration is immutable for the Run.
- Runner can reconcile after restart.
- No component gets implicit authority.
