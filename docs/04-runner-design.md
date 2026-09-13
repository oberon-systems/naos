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
| `NAOS_AGENT_IMAGE_DIR` | Image cache; default `$XDG_DATA_HOME/naos/vms`, else `~/.local/share/naos/vms` |
| `NAOS_AGENT_VM_DIR` | One directory per VM; default `$XDG_STATE_HOME/naos/runs`, else `~/.local/state/naos/runs` |
| `NAOS_AGENT_QEMU_BINARY` | QEMU system emulator, default `/usr/bin/qemu-system-x86_64` |
| `NAOS_AGENT_QEMU_IMG` | `qemu-img`, default `/usr/bin/qemu-img` |
| `NAOS_AGENT_IMAGE_MAX_BYTES` | Largest image the agent downloads, default `8589934592` |

The agent never looks for a `.env` file on its own. A file dropped into the
working directory could otherwise redirect the API URL the agent trusts.

The agent runs as an unprivileged user, typically a systemd user unit, so its
default directories are that user's. Runtime paths must be absolute and free of
commas and control characters, because they end up inside QEMU options. The
image and VM directories are created with mode 0700, and the agent refuses to
start when either is a symlink or writable by group or others. Without `HOME`
and without explicit directories, it refuses to start as well.

A systemd unit needs `KillMode=process`: QEMU runs in its own process group
but in the unit's cgroup, and stopping the agent must leave VMs running for the
next start to reconcile.

Credential and enrollment files readable by group or others, or reached
through a symlink, are refused. The HTTP client follows no redirects.

## Reconcile rules

Each cycle lists local VMs, heartbeats, fetches the desired state and applies
this table. While the runtime cannot list VMs, the heartbeat offers capacity
0 and nothing else runs.

| Desired status | Local VM | Action |
|---|---|---|
| PENDING | running or missing | claim, create or reuse the VM, report STARTED |
| PENDING | dead | destroy it, then claim and create |
| STARTING | running or missing | create or reuse the VM, report STARTED |
| STARTING | dead | destroy it, then create |
| STARTED | running | none |
| STARTED | dead or missing | report FAILED `vm lost`, destroy a dead VM |
| STOPPING | any | stop the VM, report COLLECTING |
| COLLECTING, WAITING_MERGE | any | none, the overlay is kept |
| terminal | present | destroy as orphan |
| not desired | present | destroy as orphan |

Creating a VM is idempotent by `run_id`, and a second VM for the same Run is
destroyed. A claim rejected by the API creates nothing. When the desired
state cannot be fetched, no VM is touched. A start that fails reports FAILED
`vm start failed` and removes whatever it created.

A VM is running while a process of the agent's own user carries
`-name naos-<vm_id>` on its command line; a VM directory without one is dead.
Matching the name rather than a stored pid survives agent restarts and pid
reuse.

## Runtime

The QEMU runtime turns one desired Run into one VM. Starting a Run takes these
steps, and any failure stops it and removes the VM directory:

1. refuse the Run when any policy is set: mounts, network, shell and
   MCP are not enforced by this runtime yet, so they fail closed;
2. download the image from the `image_url` the API returned into the cache
   unless a file already has its name: the agent follows at most five
   redirects and never sends its runner token there, the download goes to a
   0600 temporary file, is hashed while it streams, and is renamed to
   `sha256-<hex>.qcow2` with mode 0444 only when the digest matches;
3. open the cached image, refuse a symlink or a group or others writable file,
   and hash it again through the open descriptor on every start; a mismatch
   removes the file;
4. read the image with `qemu-img info -f qcow2` and refuse one that names a
   backing file or is larger than `disk_gib`;
5. create an empty qcow2 overlay of `disk_gib` in the VM directory;
6. start QEMU in its own process group, not daemonized, with the base image
   reopened through `/proc/<agent pid>/fd/<n>`, the descriptor that was
   hashed;
7. wait up to 30 seconds for QMP to answer.

Stopping sends `system_powerdown` over QMP, waits 30 seconds, then kills QEMU;
the overlay stays for collection. Destroying kills QEMU and removes the VM
directory, and repeating it is harmless.

```text
$NAOS_AGENT_VM_DIR/vm_<32 hex>/
  vm.json        vm_id, run_id, image id and digest, mode 0600
  session.json   run_id, vm_id, agent; handed to the guest through fw_cfg
  overlay.qcow2  the guest's writable disk
  qmp.sock       QEMU monitor
  console.sock   guest ttyS0
  boot.log       guest ttyS1
  qemu.log       QEMU stderr, mode 0600
```

The runtime writes audit events `image_cached`, `image_rejected`,
`vm_created`, `vm_stopped` and `vm_destroyed`, each with its `run_id`, `vm_id`
or digest.

## Console

The console of a running VM is its `ttyS0`, where the guest image attaches the
agent's tmux session. From the runner host:

```bash
naos-agent console run_0123456789abcdef0123456789abcdef
```

The command reads only the runtime settings above, connects to
`console.sock`, puts the terminal in raw mode and detaches on `Ctrl-]`. It
writes a `console_attached` audit event. The socket sits in a 0700 directory,
so only the runner's user can open it. Forwarding the console to the web UI
through the API is a later step.

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
