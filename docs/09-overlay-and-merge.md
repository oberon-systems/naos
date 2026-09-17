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
