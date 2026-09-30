# Open vhost-vsock

A Run with a model policy reaches its model gateway over vsock, so QEMU opens
`/dev/vhost-vsock` for it, and so does the runner's preflight. Both run as the
runner's user. Distributions create the device as `root:kvm` with mode `0660`,
so without access the runner fails the Run with `a model policy needs read and
write access to /dev/vhost-vsock`.

This runbook grants the runner's user read and write on that one device
through an ACL, and nothing else. Adding the user to the `kvm` group would
work too, but it also opens `/dev/vhost-net` and `/dev/udmabuf` to every
process of that user. What the grant still allows is listed under
[Host risks](../01-security-model.md#host-risks).

## Procedure

Run this as the user the runner runs as; `$USER` is written into the rule.
The module is loaded at boot, and the udev rule sets the ACL each time the
kernel announces the device.

```bash
echo vhost_vsock | sudo tee /etc/modules-load.d/naos-vhost-vsock.conf
printf 'ACTION=="add", SUBSYSTEM=="misc", KERNEL=="vhost-vsock", RUN+="/usr/bin/setfacl -m u:%s:rw /dev/vhost-vsock"\n' "$USER" | sudo tee /etc/udev/rules.d/70-naos-vhost-vsock.rules
sudo modprobe vhost_vsock
sudo udevadm control --reload
sudo udevadm trigger --action=add --name-match=vhost-vsock
```

## Verification

```bash
getfacl -p /dev/vhost-vsock
python3 -c 'open("/dev/vhost-vsock", "r+b").close(); print("vhost-vsock ok")'
timeout 3 qemu-system-x86_64 -nodefaults -display none -machine q35 -accel kvm -S -device vhost-vsock-pci,guest-cid=4242
echo "exit $?"
ss --vsock -lnp
```

Expected: a `user:<you>:rw-` line in the ACL, `vhost-vsock ok`, no error
from QEMU and `exit 124`, because QEMU was still running when `timeout`
stopped it. `unable to set guest cid: Address already in use` means another
VM holds CID 4242; pick another number.

`ss` must list no listener while no Run is up, and only the runner's ports,
one per Run with a model policy, while Runs are up. Any other vsock service
on this host is reachable from a Run's guest; stop it or use another host
for the runner.

Reboot once and repeat `getfacl -p /dev/vhost-vsock` to see the ACL come
back on its own.

## Rollback

```bash
sudo rm /etc/udev/rules.d/70-naos-vhost-vsock.rules /etc/modules-load.d/naos-vhost-vsock.conf
sudo udevadm control --reload
sudo setfacl -x "u:$USER" /dev/vhost-vsock
getfacl -p /dev/vhost-vsock
```

Expected: no `user:<you>` line left in the ACL.

## Script

`scripts/vhost-vsock-install.sh` runs the same steps as the sections above,
as the user the runner runs as:

| Command | Does |
|---|---|
| `scripts/vhost-vsock-install.sh` | the procedure, then the check |
| `scripts/vhost-vsock-install.sh check` | the verification; it fails with a reason on the ACL, the open, QEMU or a vsock listener that is not the runner's |
| `scripts/vhost-vsock-install.sh remove` | the rollback |

A passing check prints one green `[ok]` line per check, then
`vhost-vsock ok for <you>`; a failing one prints a red `[fail]` line with the
reason. QEMU's own output shows only when it fails. Run the check as root to
see the owners of other users' vsock listeners.
