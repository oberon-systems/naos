# 14 - Install

A naos release, tagged `X.Y.Z` or `X.Y.Z-rcN`, is the stack as one version: the compose file
with the api and web images pinned by digest, and two installers that know the
runner and the VM image of that release. It is made of the newest component
releases (`naos-api-*`, `naos-web-*`, `runner-*`, `image-*`) at the time it is
tagged. This document installs, upgrades and checks a stack and its runners.

- [Install the stack](#install-the-stack)
- [Install a runner](#install-a-runner)
- [Upgrade](#upgrade)
- [Uninstall](#uninstall)
- [Check a host](#check-a-host)
- [Cut a release](#cut-a-release)

## Install the stack

The stack needs [Docker Engine](https://docs.docker.com/engine/install/) with
the compose plugin, and a user in the `docker` group. Run the installer as that
user; it uses `sudo` only for the system layout.

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh
```

Without flags it asks where the stack goes, which address and ports to publish
and whether to install the systemd unit. Every question has a flag, and `--yes`
takes the defaults:

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh -s -- --layout system --bind 127.0.0.1 --yes
```

| Layout | Compose, `.env`, tokens | Database | Unit |
|---|---|---|---|
| `xdg` | `~/.config/naos/stack` | `~/.local/share/naos/stack` | `naos-stack.service`, user unit |
| `system` | `/opt/naos` | `/opt/naos/db` | `naos-stack.service`, system unit |

The installer runs the doctor first and stops on any problem. Then it downloads
the release files, checks them against `SHA256SUMS`, writes `.env` and the two
tokens under `secrets/` when they do not exist yet, pulls the images, starts the
unit and registers the VM image of the release. It prints the web and api urls,
the token files and the command that installs a runner. A user unit needs
linger to outlive your login, and the installer asks for it.

`--release X.Y.Z` installs another release than the latest. The script also
runs from a download (`sh install-stack.sh`) or from a checkout
(`sh release/install-stack.sh`); either way it installs the latest release
unless told otherwise.

## Install a runner

A runner host needs Linux on x86_64 with KVM, `/dev/vhost-vsock`, QEMU, git,
virtiofsd 1.13 or newer and unprivileged user namespaces; the doctor names
whatever is missing. Run the installer as the user the runner runs as, with the
enrollment token the stack installer wrote:

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-runner.sh | sh -s -- --api-url http://192.0.2.10:8080 --enrollment-token-file ./enrollment
```

Without a token file it asks for the token on the terminal and does not echo
it. The token never goes on the command line.

| Layout | Binaries | Settings and token | State | Unit |
|---|---|---|---|---|
| `xdg` | `~/.local/share/naos/bin` | `~/.config/naos` | `~/.local/state/naos/agent` | user unit |
| `system` | `/opt/naos/runner/bin`, `/usr/local/bin/runner` | `/opt/naos/runner` | `/opt/naos/runner/state` | system unit, `User=` you |

The other flags are `--name`, `--capacity`, `--zone`, `--labels`,
`--systemd`/`--no-systemd`, `--linger`/`--no-linger`, `--release` and `--yes`;
`--help` lists them. The settings land in `runner.env`, which the unit hands
to the runner through `NAOS_AGENT_ENV_FILE`.

## Upgrade

To update, run the same install command again: the installer updates what is
installed to the new release. It finds the layout and the settings of what is
installed and asks nothing it already knows. The stack keeps `.env`, the tokens and the
database, saves the old `compose.yaml` as `compose.yaml.prev`, pulls the new
images, migrates the database, restarts the unit and registers the new VM
image. The runner installs the new binary next to the old ones, moves the
`runner` link and restarts its unit; pointing the link back is the rollback.

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-runner.sh | sh
```

Arguments reach a piped script only after `sh -s --`; `sh uninstall` or
`sh doctor` makes `sh` look for a file of that name.

## Uninstall

`uninstall` removes everything the installer put on the host. It lists what
goes and asks first; `--force` removes without asking, and without a terminal
it is required.

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-runner.sh | sh -s -- uninstall
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh -s -- uninstall --force
```

| Installer | Removed |
|---|---|
| `install-runner.sh` | the unit, the `runner` link, the binaries, `runner.env`, the enrollment token, the state |
| `install-stack.sh` | the unit, the containers, volumes and images, `compose.yaml`, `.env`, the tokens, the database |

The api keeps the record of a removed runner. A user unit
leaves linger on; `loginctl disable-linger` turns it off.

## Check a host

Both installers run their doctor on their own:

```bash
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh -s -- doctor
curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-runner.sh | sh -s -- doctor
```

Expected on a ready host:

```text
install-stack: doctor: the host can run the stack
install-runner: doctor: the host can run the runner
```

Otherwise each problem is listed with its fix, and the exit status is 1.

## Cut a release

Release the components first, then bump the stack. `cz bump` in `release/`
writes the section of `release/CHANGELOG.md`: the api, web, runner and image
commits since the previous release, grouped by type.

```bash
cd release
cz bump
git push origin main "$(sed -n 's/^  version: //p' .cz.yaml)"
```

A release candidate is bumped the same way with `cz bump 1.0.0-rc0` for the
first one and `cz bump --prerelease rc` for the next. It is published as the
latest release too, so the installers pick it up.

The tag starts `.github/workflows/release.yml`. It takes the newest release of
every component, pins the api and web images by digest in `compose.yaml`,
writes the runner and image versions into the installers and publishes them as
the latest release. Component releases are published as not latest, so
`releases/latest` always names a naos release.
