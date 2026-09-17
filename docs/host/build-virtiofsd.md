# Build virtiofsd

A Run with a workspace needs [virtiofsd](https://gitlab.com/virtio-fs/virtiofsd)
1.13 or newer, because only that version can refuse writes with `--readonly`.
Use this runbook on a host whose `virtiofsd` package is older, such as Ubuntu
24.04 with 1.10. It builds virtiofsd 1.14.0 and puts it in place of the packaged
binary at `/usr/libexec/virtiofsd`, the path the runner uses by default.

The path matters on Ubuntu: the packaged AppArmor profile lets only
`/usr/libexec/virtiofsd` create the user namespaces `--sandbox namespace`
needs. `dpkg-divert` moves the packaged binary aside, so a package upgrade does
not overwrite the new one. The build needs `cargo`.

## Procedure

```bash
sudo apt install build-essential libseccomp-dev libcap-ng-dev uidmap
cargo install virtiofsd --version 1.14.0 --locked --root "$HOME/.cache/naos/virtiofsd"
sudo dpkg-divert --local --rename --divert /usr/libexec/virtiofsd.distrib --add /usr/libexec/virtiofsd
sudo install -D -m 0755 "$HOME/.cache/naos/virtiofsd/bin/virtiofsd" /usr/libexec/virtiofsd
```

## Verification

```bash
/usr/libexec/virtiofsd --version
dpkg-divert --list /usr/libexec/virtiofsd
```

Expected: `virtiofsd 1.14.0`, and a line saying the local diversion of
`/usr/libexec/virtiofsd` goes to `/usr/libexec/virtiofsd.distrib`.

Start virtiofsd the way the runner does, for three seconds, on an empty
directory:

```bash
mkdir -p /tmp/naos-virtiofsd-check/share
timeout 3 /usr/libexec/virtiofsd --socket-path=/tmp/naos-virtiofsd-check/fs.sock --shared-dir=/tmp/naos-virtiofsd-check/share --readonly --sandbox=namespace --cache=never --uid-map=":1000:$(id -u):1:" --gid-map=":1000:$(id -g):1:"
echo "exit $?"
ls /tmp/naos-virtiofsd-check
rm -rf /tmp/naos-virtiofsd-check
```

Expected: no line containing `ERROR`, the last log line
`Waiting for vhost-user socket connection...`, `exit 124` because virtiofsd was
still waiting for QEMU when `timeout` stopped it, and `fs.sock` in the listing.
An `ERROR` about id mappings means `uidmap` or the ranges in `/etc/subuid` and
`/etc/subgid` are missing for your user.

These warnings are normal for an unprivileged virtiofsd and need no action:

```text
WARN  virtiofsd::sandbox] Couldn't set the process uid as root: -1
WARN  virtiofsd::sandbox] Couldn't set the process gid as root: -1
WARN  virtiofsd::passthrough] Failed to open file handle for the root node: Operation not permitted (os error 1)
WARN  virtiofsd::passthrough] File handles do not appear safe to use, disabling file handles altogether
```

Only your own uid and gid are mapped into the sandbox, so virtiofsd keeps
running as your user rather than root, and the guest sees your files as owned
by `naos`. File handles need a privilege an unprivileged process lacks, so
virtiofsd opens files by path instead; `--readonly` sharing is unaffected.

## Rollback

```bash
sudo rm /usr/libexec/virtiofsd
sudo dpkg-divert --local --rename --remove /usr/libexec/virtiofsd
/usr/libexec/virtiofsd --version
```

Expected: the version of the `virtiofsd` package again.
