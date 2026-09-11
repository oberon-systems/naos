# Prompt 08 — Safe Merge

Read `AGENTS.md` and `docs/09-overlay-and-merge.md`.

Implement merge policies: always, ask, never.

Never blindly copy an overlay into the host. Reject absolute paths, traversal, symlink/hardlink escapes, special files, mount escapes, and unauthorized root changes.

Prefer staging and atomic final application.

Acceptance: malicious overlay contents cannot modify files outside the authorized workspace; `never` changes nothing; `ask` requires approval.
