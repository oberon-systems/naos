# Prompt 05 — Shell Gate

Read `AGENTS.md` and `docs/07-shell-gate.md`.

Implement structured host capabilities: read_file, list_dir, grep, git_status, git_diff.

Confine all paths to authorized mount roots. Prevent traversal, symlink/hardlink escapes, cwd escape, and injection.

Do not implement `/bin/sh -c` as the authorization mechanism.

Acceptance: every capability has authorization, validation, resource limits, and negative security tests.
