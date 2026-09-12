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
packer_<version> tag -> GitHub release
  -> POST /api/v1/images (API downloads, checks sha256, stores)
  -> Run spec names image id and digest (image must be READY)
  -> runner downloads through the API (checks sha256, caches read-only)
  -> every VM start hashes the cached file through the descriptor QEMU opens
  -> read-only base + disposable qcow2 overlay
```

- The build and release are described in
  [packer/README.md](../packer/README.md).
- The API side, including the store and the settings, is in
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
| `-chardev file` + `-serial` | `ttyS1` into `boot.log` |
| `-qmp unix:qmp.sock` | monitor for readiness and power-down |
| `-fw_cfg name=opt/naos/session` | per-Run parameters from `session.json` |

There is no `-virtfs`, `-fsdev`, `-drive`, `-hda`, `-netdev`, `-kernel`,
`-cdrom` or `-usb`, and a unit test fails when one appears. Host mounts are not
part of this runtime yet: a Run with a mount policy does not start.

## Console and session

The guest has two serial ports. `ttyS0` is interactive: the runner exposes it
as `console.sock`, and the image logs `naos` in there and attaches to the
agent's tmux session. `ttyS1` carries kernel messages and the isolation probes
into `boot.log`.

The guest reads `/sys/firmware/qemu_fw_cfg/by_name/opt/naos/session/raw` for
its per-Run parameters: `run_id`, `vm_id` and `agent`, which selects `claude`
or `gemini`. fw_cfg is read-only for the guest and needs no disk or host path.

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

- The network gate ([P1](12-roadmap.md)) will carry provider traffic and add
  credentials on the host side, so a key never enters the VM. The session will
  point the agents at the gate instead of the provider.
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

The boot test `real_image_boots_probes_and_is_cleaned_up` covers the first four
with a real image, together with the unit tests of the runner:

```bash
make test-qemu IMAGE=build/agents/naos-agents-0.1.0.qcow2
```

## Hardening backlog

seccomp/AppArmor, minimal devices, signed images, digest verification, scanning, quotas, QEMU process isolation.
