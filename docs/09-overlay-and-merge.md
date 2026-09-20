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

The host side at collection time is the base a merge checks against. A
`modified` file adds `base_sha256` and `base_mode`, a `modified` symlink adds
`base_target`, and a `renamed` file adds the `base_mode` of the file it
replaces; a `deleted` entry describes the host entry itself. An entry whose
path, or `from`, runs on the host or in CI once merged carries
`"sensitive": true`: anything under `.git`, `.github`, `.gitlab`,
`.circleci`, `.buildkite` or `.husky`, Makefiles, Dockerfiles, compose
files, package and build manifests and Terraform files.

A hardlinked file, a device, fifo or socket, setuid, setgid or sticky bits, a
file larger than the disk, a non-UTF-8 name, a path over 4096 bytes and a
symlink whose target is absolute or climbs above the workspace root are
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

## Merge

After collection the runner sends `diff.json` to the API, and the Run moves
from COLLECTING to WAITING_MERGE. The merge policy of the Run spec decides
what happens next:

| Policy | Decision |
|---|---|
| `never` | nothing is selected, the Run completes with the workspace untouched |
| `always` | every path that is not `rejected` is selected, unless one entry is `sensitive` |
| `ask` | the operator decides, the default |

An `always` diff with a sensitive entry waits for the operator like `ask`.
The operator reads the diff with `GET /api/v1/runs/{run_id}/merge` and
selects paths with `POST /api/v1/runs/{run_id}/merge`, or merges nothing
with `POST .../merge/reject` ([03](03-api-design.md#merge)). A selection
names whole paths: a `rejected` path, a deleted directory without the
deleted entries under it or the renames out of it, and an entry inside a
created directory without that directory are refused with 422.

The runner applies a decision in four steps, all under the workspace fd
reached one component at a time with `O_NOFOLLOW`:

1. Precheck: every selected entry is compared with the host. A `modified`
   or `deleted` entry needs its base, a created one needs an empty path. A
   mismatch is a conflict, and nothing is written.
2. Staging: new file contents are read from the upper disk into a 0700
   `.naos-merge-<hex>` directory in the workspace root, and their size and
   sha256 are checked against the diff again.
3. Apply: removals deepest first, then new directories, then files. Each
   step is checked against the host again, written to a journal and fsynced
   before it runs, and never overwrites: a host entry that is replaced or
   deleted is moved into staging, and new entries land with
   `RENAME_NOREPLACE`.
4. Commit: the journal records the report, the moved host entries are copied
   to `merge/backup/` in the VM directory, and staging is removed.

Any failure during apply undoes the journal newest step first and reports
the failed path as a conflict. A runner that stops mid-merge finds the
journal on its next pass: it undoes an uncommitted one before trying again
and finishes a committed one. Nothing on the host is ever deleted by a
merge; it is moved into the backup.

## Conflicts

A conflict keeps the Run in WAITING_MERGE: the API stores the conflicts,
clears the decision and waits for a new one. The new decision resolves each
conflicting path under `resolutions`:

| Resolution | Effect |
|---|---|
| `skip` | the host keeps its version, the agent's change is dropped |
| `take` | the agent's version is applied, the host's current one goes to `merge/backup/` |
| `export` | the host is left alone, the agent's version is written to `merge/export/` |

`skip` and `export` also apply to paths without a conflict. A merge that
applies reports `applied`, `skipped`, `exported` and `backed_up`, and the Run
completes.

## Retention

The agent's changes outlive the Run. Destroying a VM that has an upper disk,
because its Run ended or it became an orphan, moves `upper.img`,
`diff.json`, `merge/` and `vm.json` to `archive/<vm_id>/` in the runner's VM
directory and deletes the rest. Nothing removes the archive yet: an operator
deletes a directory once its backup and exports are no longer needed.

```bash
ls "$NAOS_AGENT_VM_DIR/archive"
```

The merge is covered by the merge tests in
`packages/runner/src/libs/overlay/tests.rs`: every change applied with its
host version kept, an empty decision, a host changed after collection, the
three resolutions, a directory swapped for a symlink before the merge, a
failure that rolls back and an interrupted journal. The smoke test edits the
host file the guest also changed, checks that the first decision conflicts
without writing anything, takes the agent's version and checks the
workspace, the backup and the archive.
