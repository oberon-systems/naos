# 05 — VM and QEMU

## Prompt

Build the minimum secure VM runtime before convenience features.

Each Run gets a fresh VM with immutable base image and disposable writable overlay.

Do not expose host root, Docker socket, SSH agent, cloud credentials, arbitrary host devices, or broad directories unless explicitly authorized.

Images use immutable digests.

## Image lifecycle

An image reaches a VM through three owners, and its digest is checked at each
hand-over.

```text
image-<version> tag -> GitHub release
  -> POST /api/v1/images (id, version, digest, url; the API keeps the entry)
  -> Run spec names image id and digest (image must be registered)
  -> runner downloads from the url (checks sha256, caches read-only)
  -> every VM start hashes the cached file through the descriptor QEMU opens
  -> read-only base + disposable qcow2 overlay
```

- The build and release are described in
  [packer/README.md](../packer/README.md).
- The API side, the image catalog, is in
  [03 - API Design](03-api-design.md#images).
- The runner side, the cache and the start sequence, is in
  [04 - Runner Design](04-runner-design.md#runtime).

The runner refuses an image whose qcow2 header names a backing file. Such an
image would make QEMU open a host path chosen by whoever built it.

## QEMU command line

The runner builds the complete command line itself and runs it without a
shell. Everything the guest can reach is on this list:

| Option | Purpose |
|---|---|
| `-name naos-<vm_id>` | identifies the process for listing and killing |
| `-nodefaults -no-user-config` | no default devices, no host QEMU config |
| `-machine q35 -accel kvm -cpu host` | KVM only; no software fallback |
| `-smp <cpu> -m <memory_mib>M` | resources from the Run spec |
| `-display none -nic none` | no display and no network device |
| `-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny` | seccomp filter on QEMU itself |
| `-blockdev` base, `read-only` | the verified image through `/proc/<pid>/fd/<n>` |
| `-blockdev` overlay with `backing` set to the base | the only writable disk, inside the VM directory |
| `-device virtio-blk-pci,drive=disk` | the single disk the guest sees |
| `-chardev socket` + `-serial` | `ttyS0` on `console.sock` |
| `-device virtio-serial-pci` + `-device virtserialport,name=naos.mcp` | the MCP port on `mcp.sock` |
| `-chardev file` + `-serial` | `ttyS1` into `boot.log` |
| `-qmp unix:qmp.sock` | monitor for readiness and power-down |
| `-fw_cfg name=opt/naos/session` | per-Run parameters from `session.json` |

A Run whose mount policy has a workspace adds:

| Option | Purpose |
|---|---|
| `-object memory-backend-memfd,share=on` + `-machine q35,memory-backend=mem` | guest RAM virtiofsd can map |
| `-chardev socket,path=fs.sock` + `-device vhost-user-fs-pci,tag=naos-workspace` | the workspace, shared read-only by virtiofsd |
| `-blockdev` raw `upper.img` + `-device virtio-blk-pci,serial=naos-upper` | `rw` only: the disk the guest's workspace writes land on |

There is no `-virtfs`, `-fsdev`, `-drive`, `-hda`, `-netdev`, `-kernel`,
`-cdrom` or `-usb`, and a unit test fails when one appears. Home entries of a
mount policy get no device: the shell gate serves them read-only
([07](07-shell-gate.md)).

## Workspace

The runner never copies a workspace. It starts virtiofsd over the host
directory with `--readonly --sandbox namespace --cache never`, mapping its own
uid and gid onto the guest's `naos` (1000), so the host refuses every write
whatever the guest mounts. That needs virtiofsd 1.13 or newer, which
`virtiofsd --version` must report before the Run starts, `newuidmap` and
unprivileged user namespaces for the namespace sandbox; a host whose package is
older builds it with [build-virtiofsd.md](host/build-virtiofsd.md). The host path must be a directory reached without
symlinks.

In the guest, `naos-workspace` mounts the share at `/run/naos/lower`. In `rw`
mode it formats the upper disk on first use, mounts it at `/run/naos/upper`
and puts an overlay at `/naos/<name>` with `redirect_dir`, `metacopy`, `index`
and `xino` off, so every change lands on the upper disk as whole files and
whiteouts the runner can read ([09](09-overlay-and-merge.md#collection)).
In `ro` mode the share is bound read-only at `/naos/<name>`. The agent's tmux session starts there.

## Console and session

The guest has two serial ports and one virtio-serial port. `ttyS0` is
interactive: the runner exposes it as `console.sock`, and the image logs `naos`
in there and attaches to the agent's tmux session. `ttyS1` carries kernel
messages and the isolation probes into `boot.log`. The virtio-serial port
`naos.mcp` is the only way from the agent to the gates: the runner serves it on
`mcp.sock`, and [08](08-mcp-gate.md) describes what flows over it.

At boot `naos-probe` finds the port by name and sends it a `ping`; a missing
port or a missing answer fails the probe, so a VM that cannot reach its gates
never counts as booted. `naos-session` then hands the port to `naos` as
`/run/naos/mcp`, and `/usr/local/bin/naos-mcp` pipes stdio into it. Claude Code
and Gemini CLI register that command as the MCP server `naos`.

The guest reads `/sys/firmware/qemu_fw_cfg/by_name/opt/naos/session/raw` for
its per-Run parameters: `run_id`, `vm_id`, `agent`, which selects `claude`
or `gemini`, and `workspace` with `workspace_mode` when there is one. fw_cfg is read-only for the guest and needs no disk or host path.

## Guest user and instructions

The agent runs as `naos`, an account without a password, sudo or doas, with
home `/home/naos`. The shipped image has no sshd and locked `root` and `naos`
passwords; the only way in is the runner's console.

The image installs default instructions for Claude Code and Gemini CLI that
describe this environment. They tell the agent that it has no direct network,
that outside access goes only through MCP gates, and that its changes reach
the host only through review and merge. They are guidance: prompt instructions
are never enforcement, and the boundary is the command line above and the
gates.

## Credentials and gates

No credential is baked into an image or passed in `session.json`. Until the
gates exist, an agent starts in its session but cannot log in to its provider,
and that is the expected state of this runtime.

- The network gate ([06](06-network-gate.md)) enforces the policy host-side and
  will carry provider traffic with credentials attached there, so a key never
  enters the VM. Its only caller today is the `http_request` tool of the MCP
  broker ([08](08-mcp-gate.md)); the session does not yet point the agents at
  the gate instead of the provider.
- The MCP gate ([P3](12-roadmap.md)) will hand out short-lived per-Run tokens
  where a provider cannot be proxied.
- An interactive login inside the VM, such as an OAuth flow, will only be
  possible as an explicit capability of the Run.

## Acceptance

Acceptance tests must verify:

- approved image starts;
- overlay is disposable;
- VM is destroyed after Run;
- unauthorized host paths are invisible;
- Runs cannot access one another;
- runner APIs are unreachable unless explicitly allowed.

| Property | Verified by |
|---|---|
| approved image starts | `real_image_boots_probes_and_is_cleaned_up`, `make smoke` |
| overlay is disposable | `base_is_read_only_under_a_writable_overlay`; the boot test destroys every VM directory with its overlay and re-verifies the base digest |
| VM is destroyed after Run | the boot test, `failed_qemu_start_leaves_no_vm_behind` |
| unauthorized host paths are invisible | `no_host_path_reaches_the_guest_implicitly`, `a_workspace_reached_through_a_symlink_is_refused`; the probe fails on an unexpected disk, any 9p mount and any virtiofs mount but the workspace share |
| the workspace is read-only on the host | `a_workspace_is_shared_read_only_and_released_when_qemu_fails`, `a_workspace_needs_a_virtiofsd_that_can_refuse_writes`; the probe remounts the share rw and fails if a write gets through, and in `rw` mode fails unless a write by `naos` lands in the overlay only; the boot and smoke tests compare the host workspace before and after |
| Runs cannot access one another | the boot test checks that no QEMU command line names another VM directory; `runs_sharing_a_guest_path_read_only_their_own_mounts` |
| runner APIs are unreachable | `vm_has_no_network_no_defaults_and_a_sandbox`; the probe fails on any interface but `lo` and on any virtio device but the disks, the serial ports and, with a workspace, the share |

The probe runs inside every booted guest, and both the boot test and the smoke
test fail on `naos-probe fail`:

```bash
make test
make test-image
make smoke
```

## Hardening backlog

seccomp/AppArmor, minimal devices, signed images, digest verification, scanning, quotas, QEMU process isolation.
