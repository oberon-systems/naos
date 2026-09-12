# Packer images

The VM images a Naos Run boots. [Packer](https://www.packer.io) builds them with the QEMU builder into `qcow2`, a [GitHub Actions](https://docs.github.com/actions) workflow publishes them on a `packer_<version>` tag, and the API imports a published image by its digest.

- [Why](#why)
- [How it is built](#how-it-is-built)
- [Build locally](#build-locally)
- [Publish a release](#publish-a-release)
- [Use an image in Naos](#use-an-image-in-naos)
- [Images](#images)

## Why

A Run is only as isolated as the machine it boots. These templates pin the distribution, the agents and the serial layout the runner depends on, and they remove every way into the guest except the runner's console.

## How it is built

Every directory holding a `*.pkr.hcl` template is an image and gets its own make targets. `base` installs [Alpine Linux](https://alpinelinux.org) from its ISO; `agents` boots the `base` artifact and adds the agents on top of it.

```text
packer/Makefile                        pinned packer, image targets
packer/base/naos-base.pkr.hcl          Alpine install, serial layout, probes
packer/base/http/answers               setup-alpine answer file
packer/base/files/naos-probe           isolation probes, written to ttyS1
packer/agents/naos-agents.pkr.hcl      agents, session, seal
packer/agents/files/install.sh         packages, agents, default instructions
packer/agents/files/seal.sh            removes sshd, host keys and passwords
.github/workflows/packer-image.yml     validate on pull request, release on tag
```

Packer logs in over ssh as root while it builds, with a random password the Makefile keeps in `build/.build-password`. The `agents` build ends with `seal.sh`, so the shipped image has no sshd, no host keys, and locked `root` and `naos` accounts. The `base` image keeps sshd for the builds derived from it and is never published.

## Build locally

You need `qemu-system-x86_64`, `qemu-img`, write access to `/dev/kvm`, `curl` and `unzip`. The Makefile installs packer 1.16.0 into `.packer/bin/1.16.0/` and its plugins into `.packer/plugins/` unless that exact version is already on `PATH`, and checks the download against HashiCorp's `SHA256SUMS`.

```bash
make packer install
make packer validate
make packer build-all VERSION=0.1.0
```

The artifacts land in `build/<image>/naos-<image>-<version>.qcow2`, next to a `manifest.json`. Both `build/` and `.packer/` are gitignored.

Boot a throwaway copy the way the runner does: no network, the console on this terminal, the probes in `build/agents-boot.log`. Quit QEMU with `Ctrl-a x`; the disk is attached with `snapshot=on`, so the artifact never changes.

```bash
make packer run-agents VERSION=0.1.0
```

Run the runner's boot test against the image:

```bash
make test-qemu IMAGE=build/agents/naos-agents-0.1.0.qcow2
```

The test passes when both VMs boot, `boot.log` shows `naos-ready` and `naos-probe ok`, a VM killed from outside is reported as not running, and the cached base image still matches its digest after the guests wrote to their disks.

## Publish a release

Push a tag named `packer_<version>`. The workflow builds both images on a KVM-enabled runner and creates a GitHub release holding `naos-agents-<version>.qcow2` and its `SHA256SUMS`.

```bash
git tag packer_0.1.0
git push origin packer_0.1.0
```

The `packer_*` tags never collide with the commitizen version tags, which carry no prefix. A pull request that touches `packer/` only runs `make -C packer validate`.

## Use an image in Naos

The API downloads images; the runner only ever fetches them from the API. Point the API at the release, with `{version}` where the version goes. GitHub redirects release downloads to a separate host, and the API follows a redirect only to a host you allow:

```bash
curl -sI https://github.com/<owner>/<repo>/releases/download/packer_0.1.0/naos-agents-0.1.0.qcow2 | grep -i '^location'
```

```text
NAOS_IMAGE_SOURCE_URL=https://github.com/<owner>/<repo>/releases/download/packer_{version}/naos-agents-{version}.qcow2
NAOS_IMAGE_SOURCE_ALLOWED_HOSTS=["<host from the location header>"]
```

Register the image with the digest from the release's `SHA256SUMS`. The API answers `202`, downloads the file and checks it; the image is usable once `GET /api/v1/images/<id>` reports `READY`.

```bash
curl -X POST https://api.example.com/api/v1/images \
  -H 'Content-Type: application/json' \
  -d '{"id": "naos-agents", "version": "0.1.0", "digest": "sha256:<hex from SHA256SUMS>"}'
```

Operator endpoints refuse every request until operator authentication exists, see [03 - API Design](../docs/03-api-design.md). A Run then names the image by `id` and `digest` in its spec; [05 - VM and QEMU](../docs/05-vm-and-qemu.md) covers what happens next.

## Images

### base

| | |
|---|---|
| Template | `base/naos-base.pkr.hcl` |
| Artifact | `build/base/naos-base-<version>.qcow2` |
| Alpine | 3.24.1, `alpine-virt` x86_64, ISO checksum pinned |
| Disk | 4G, no swap |
| Accounts | `root` with the build password; `naos` with home `/home/naos`, no password, not in `wheel` |
| ttyS0 | `agetty` logs `naos` in automatically; the runner exposes it as the console |
| ttyS1 | kernel messages and probes; the runner writes it to `boot.log` |
| Services | `naos-probe`, `acpid`, `sshd` for the build only |

`naos-probe` runs once per boot and writes its findings to `ttyS1`. They are evidence for the boot test, not enforcement: the QEMU command line is what keeps devices and host paths away from the guest.

| Line | Meaning |
|---|---|
| `naos-ready` | the guest reached the default runlevel |
| `naos-probe ok` | exactly one virtio disk, no 9p or virtiofs mount, no network interface but `lo` |
| `naos-probe fail: ...` | the checks that failed, for example `interface=eth0` |

### agents

| | |
|---|---|
| Template | `agents/naos-agents.pkr.hcl` |
| Artifact | `build/agents/naos-agents-<version>.qcow2`, compressed |
| Derives from | `build/base/naos-base-<version>.qcow2` |
| Packages | `bash`, `git`, `libgcc`, `libstdc++`, `nodejs`, `npm`, `ripgrep`, `tmux` |
| Agents | [Claude Code](https://code.claude.com/docs) 2.1.269 and [Gemini CLI](https://github.com/google-gemini/gemini-cli) 0.59.0, the `claude_code_version` and `gemini_cli_version` variables |
| Session | `naos-session` starts tmux session `agent` as `naos`, running the agent named in fw_cfg `opt/naos/session`, `claude` by default |
| Console | a login on `ttyS0` attaches to the `agent` session |
| Sealed | sshd removed from the runlevels, host keys deleted, `root` and `naos` locked, only `lo` configured |

Every agent gets default instructions that describe the environment: no direct network, outside access only through MCP gates, changes reaching the host only through review and merge, and no credentials in the VM. They are guidance for the agent, not a security boundary.

| File | Owner | Purpose |
|---|---|---|
| `/etc/claude-code/CLAUDE.md` | root | managed instructions Claude Code loads in every session |
| `/etc/claude-code/managed-settings.json` | root | `USE_BUILTIN_RIPGREP=0` for musl, `DISABLE_AUTOUPDATER=1` |
| `/home/naos/.claude/skills/naos-environment/SKILL.md` | root | the environment skill for Claude Code |
| `/home/naos/.gemini/GEMINI.md` | root | global context for Gemini CLI |
| `/home/naos/.gemini/skills/naos-environment/SKILL.md` | root | the same skill for Gemini CLI |

The image carries no login for either agent. Until a gate supplies credentials, an agent starts in its session but cannot reach its provider.
