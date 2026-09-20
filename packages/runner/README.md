# naos runner

The host-side daemon of [naos](https://github.com/oberon-systems/naos). It
leases Runs from the control-plane API, downloads the image the Run names,
boots it in a disposable [QEMU](https://www.qemu.org/) VM and enforces the
policy the Run was created with. Everything the agent inside the VM is not
granted is denied.

A runner host needs Linux on x86_64 with [KVM](https://linux-kvm.org/),
unprivileged user namespaces and
[virtiofsd](https://gitlab.com/virtio-fs/virtiofsd) 1.13 or newer.

## Install

The install script takes the newest `runner-<version>` release, checks it
against the release `SHA256SUMS` and links it as `runner`:

```bash
curl -fsSL https://raw.githubusercontent.com/oberon-systems/naos/main/packages/runner/install.sh | sh
```

From [crates.io](https://crates.io/crates/naos-runner) instead:

```bash
cargo install naos-runner
```

## Configure

Every setting is an environment variable with the `NAOS_AGENT_` prefix, read
once at start; `NAOS_AGENT_ENV_FILE` names a `.env` file loaded before them.
The runner refuses to start on a setting it cannot use.

The runner is an unprivileged user process, so its paths are that user's own.
`NAOS_AGENT_STATE_DIR` and `NAOS_AGENT_ENROLLMENT_TOKEN_FILE` have no defaults
and the rest can stay unset; the token file must be 0600 and the state
directory is created 0700.

```bash
export NAOS_AGENT_API_URL=https://api.example.com
export NAOS_AGENT_NAME=alpha
export NAOS_AGENT_CAPACITY=2
export NAOS_AGENT_STATE_DIR="$HOME/.local/state/naos/agent"
export NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$HOME/.config/naos/enrollment"
runner
```

The enrollment token is what the api checks once, at registration; afterwards
the runner uses the token the api issued it and keeps it in
`credentials.json` in the state directory.

The full table, the state layout and the console protocol are in
[docs/04-runner-design.md](https://github.com/oberon-systems/naos/blob/main/docs/04-runner-design.md).

## Build from source

```bash
cargo build --release -p naos-runner
cargo test --workspace
```

The binary lands in `target/release/naos-runner`.
