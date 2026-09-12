# 04 — Runner Design

## Prompt

Implement `naos-agent` as a Rust reconciliation/execution daemon.

## Modules

```text
api-client
lease-manager
scheduler
run-manager
qemu-manager
overlay-manager
mount-manager
gate-manager
console-server
diff-manager
reconciler
audit-log
```

## Responsibilities

Register, renew lease, receive Runs, create/manage QEMU, configure overlays/mounts/gates, proxy console, collect overlay, report lifecycle, reconcile after restart.

The API provides declarative desired state. Runner executes it exactly and never broadens permissions.

## Reconciliation

After restart:

1. register/authenticate;
2. renew lease;
3. fetch assigned active Runs;
4. inspect local VMs;
5. compare desired/actual state;
6. recover valid Runs;
7. terminate orphaned VMs;
8. report state.

Duplicate work must not create duplicate VMs.

## Configuration

The agent reads its settings from the environment and refuses to start
without them.

| Variable | Meaning |
|---|---|
| `NAOS_AGENT_API_URL` | API base URL; plain `http` only to loopback |
| `NAOS_AGENT_NAME` | Runner name, `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` |
| `NAOS_AGENT_CAPACITY` | Runs this runner accepts, 0 to 64, default 1 |
| `NAOS_AGENT_STATE_DIR` | Holds `credentials.json`, mode 0600 |
| `NAOS_AGENT_ENROLLMENT_TOKEN_FILE` | Enrollment token, mode 0600 |
| `NAOS_AGENT_ENV_FILE` | Optional `.env` file loaded before the variables above |

The agent never looks for a `.env` file on its own. A file dropped into the
working directory could otherwise redirect the API URL the agent trusts.

Credential and enrollment files readable by group or others, or reached
through a symlink, are refused. The HTTP client follows no redirects.

## Reconcile rules

Each cycle lists local VMs, heartbeats, fetches the desired state and applies
this table. While the runtime cannot list VMs, the heartbeat offers capacity
0 and nothing else runs.

| Desired status | Local VM | Action |
|---|---|---|
| PENDING | any | claim, create or reuse the VM, report STARTED |
| STARTING | any | create or reuse the VM, report STARTED |
| STARTED | present | none |
| STARTED | missing | report FAILED `vm lost` |
| STOPPING | any | stop the VM, report COLLECTING |
| COLLECTING, WAITING_MERGE | any | none, the overlay is kept |
| terminal | present | destroy as orphan |
| not desired | present | destroy as orphan |

Creating a VM is idempotent by `run_id`, and a second VM for the same Run is
destroyed. A claim rejected by the API creates nothing. When the desired
state cannot be fetched, no VM is touched.

## Lease fencing

The agent keeps a local lease deadline, measured from when each heartbeat was
sent. Once it passes without a successful renewal, the agent destroys every
local VM: the API has already failed those Runs. After a restart the deadline
starts at 60 seconds. Stopping the agent leaves VMs running for the next
start to reconcile.

A rejected runner token drops the stored credentials. The agent registers
again under a new identity, and the VMs of the old one become orphans.

## Acceptance

Runner survives API/queue restart, cleans orphan resources, and never invents permissions.
