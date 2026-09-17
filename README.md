# naos

Naos runs untrusted AI agents inside disposable [QEMU](https://www.qemu.org/)
VMs and treats every way out of the VM as a capability a Run must be granted.
The agent works on a read-only share of the host workspace, its changes land on
a separate disk instead of the host, and network, shell and MCP access go
through host-side gates. Anything not granted is denied.

The control plane is a Python API that owns Runs and their immutable policies.
A Rust runner on each host leases Runs, boots them from a
[Packer](https://www.packer.io/)-built image and enforces the policies.
[docs/00-architecture.md](docs/00-architecture.md) describes the whole design.

## Requirements

A runner host needs Linux on x86_64 with [KVM](https://linux-kvm.org/),
unprivileged user namespaces and
[virtiofsd](https://gitlab.com/virtio-fs/virtiofsd) 1.13 or newer. Install the
packages and give your user access to KVM:

```bash
sudo apt install qemu-system-x86 qemu-utils virtiofsd uidmap git
sudo usermod -aG kvm "$USER"
```

Log in again, then check the host:

```bash
/usr/libexec/virtiofsd --version
grep "^$USER:" /etc/subuid /etc/subgid
test -w /dev/kvm && echo kvm ok
```

Both `subuid` and `subgid` must list a range for your user; `useradd` creates
them by default. Debian 13 and Ubuntu 26.04 ship virtiofsd 1.13 or newer. Where
`--version` reports an older one, as on Ubuntu 24.04, build it with
[docs/host/build-virtiofsd.md](docs/host/build-virtiofsd.md); the runner
refuses a Run with a workspace until then.
