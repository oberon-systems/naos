# 09 — Overlay and Merge

## Prompt

Implement filesystem change collection and safe merge as a security boundary.

Lifecycle:
VM -> writable overlay -> VM destroyed -> overlay collected -> structured diff -> merge policy.

## Workspace overlay

The overlay that holds the agent's changes is not the VM's system disk. The
host workspace reaches the guest read-only over virtiofs, and in `rw` mode the
guest mounts an overlayfs whose upper directory lives on `upper.img`, a disk of
its own in the VM directory ([05](05-vm-and-qemu.md#workspace)). The upper disk
holds only what changed: a whole file for every created or modified one, a
whiteout for every deletion. It stays in the VM directory after the VM stops
and is what collection reads; its contents are untrusted.

## Collection

The host kernel never mounts the upper disk. The runner parses it with its own
read-only ext4 reader and fails the collection on anything outside a narrow
format: incompatible features other than `filetype`, `extent`, `64bit` and
`flex_bg`, a filesystem not unmounted cleanly, block-mapped or inline-data
inodes, and any offset, length or extent outside the disk. The guest mounts
the overlay with `redirect_dir`, `metacopy`, `index` and `xino` off, so an
overlay attribute other than `opaque=y` and the informational ones fails it
too.

The runner walks `data/` in byte order and looks up every path in the host
workspace one component at a time with `O_NOFOLLOW`, so a host symlink is
reported, never entered. A file whose content and permission bits match the
host is not a change. A whiteout deletes what the host has at its path, a
whole directory included, and an opaque directory deletes the host entries it
does not hold again.

| Change | Meaning |
|---|---|
| `created` | the path is absent on the host |
| `modified` | the host has an entry of the same kind that differs |
| `deleted` | the host entry is gone; a kind change is `deleted` then `created` |
| `renamed` | one deleted and one created non-empty file share size and sha256, and no other file does |
| `rejected` | kept out of the diff with a `reason` |

Each entry carries `path` (relative to the workspace), `change` and `kind`
(`file`, `dir`, `symlink`, `other`); files add `size`, `sha256` and `mode`
(permission bits), symlinks add `target`, renames add `from`. Entries are
sorted by path bytes, so the same disk always yields the same `diff.json`.
File contents stay on the upper disk.

A hardlinked file, a device, fifo or socket, setuid, setgid or sticky bits, a
file larger than the disk, a non-UTF-8 name and a path over 4096 bytes are
`rejected`, and collection goes on. A directory linked twice, nesting deeper
than 256 levels or more than 100 000 entries fail the collection, and the Run
fails with `collection failed`.

Policies:

- always
- ask
- never

Diff should detect created, modified, deleted, and safely detectable renamed files.

Never blindly copy overlay into host.

Reject or safely handle absolute paths, traversal, symlinks, hardlinks, special files, ownership/permission abuse, mount escapes, and TOCTOU.

Prefer staging before final application.

Treat `.git/hooks`, CI workflows, package scripts, Makefiles, Dockerfiles, and deployment files as security-relevant.

Acceptance: deterministic diff, workspace confinement, selected-file merge, safe failure behavior.

The reader and the diff are covered by `packages/runner/src/libs/overlay/tests.rs`,
which builds ext4 disks with `mkfs.ext4` and `debugfs`: the four changes, an
opaque directory, whiteouts, symlinks that are never followed, the rejected
objects and disks damaged byte by byte. The smoke test collects a real Run: the
guest modifies, creates, deletes and renames a file, adds a symlink and a fifo
and replaces a directory, and the run fails unless `diff.json` holds every one
of those changes and no leftover of the boot probe, the host workspace is
unchanged and the upper disk is still there.
