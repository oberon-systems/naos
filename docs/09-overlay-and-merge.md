# 09 — Overlay and Merge

## Prompt

Implement filesystem change collection and safe merge as a security boundary.

Lifecycle:
VM -> writable overlay -> VM destroyed -> overlay collected -> structured diff -> merge policy.

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
