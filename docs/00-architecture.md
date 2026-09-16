# 00 — Architecture

## Prompt

Implement Naos around a Run abstraction.

## Stack

- Python 3.12+, FastAPI, Pydantic, SQLModel on any database SQLAlchemy
  supports; plain tables, no triggers, procedures or foreign keys.
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
image:
  id: image_alpha
  digest: sha256:...
runtime:
  cpu: 4
  memory_mib: 8192
  disk_gib: 30
mounts:
  policy: mntpol_...
network:
  policy: netpol_...
shell:
  policy: shellpol_...
mcp:
  policy: mcppol_...
merge:
  policy: ask
timeout: 7200
```

The API assigns `run_id`; the client never sends it or a status. A missing
policy reference grants nothing. The spec and every referenced policy are
immutable from creation: no endpoint or service writes them after the insert.
Changing the security boundary requires a new Run.

## Mount policy

Mounts are a policy like network, shell and MCP. The client sends
host paths; the API resolves the guest paths, so the runner never invents
them.

```yaml
workspace:
  host_path: /srv/projects/alpha
  mode: rw
home:
  - host_path: /srv/agent-home/claude
    guest_path: .claude
    mode: ro
```

The workspace mounts at `/naos/<basename>`, here `/naos/alpha`, and is the
agent working directory. Home entries mount under `/home/naos`, the home of the
default VM user `naos`; this is where agent settings and credentials go. Host
paths must be absolute, normalized and inside `NAOS_ALLOWED_MOUNT_ROOTS`; the
default is empty, which denies every mount.

The guest paths are not a filesystem the VM sees yet. They are the namespace
the shell gate serves ([07](07-shell-gate.md)), read-only and on request; the
VM has no mount device of its own.

## Acceptance criteria

- Run is the primary lifecycle entity.
- VM is disposable.
- Security configuration is immutable for the Run.
- Runner can reconcile after restart.
- No component gets implicit authority.
