# 07 — Shell Gate

## Prompt

Implement host-side agent capabilities using structured operations.

Prefer:

- read_file(path)
- list_dir(path)
- grep(path, pattern)
- git_status(path)
- git_diff(path)

Validate capability, path, arguments, mount root, and resource limits.

Do not use `/bin/sh -c <agent input>` as an authorization model.

If arbitrary exec is required, enforce executable allowlist, argument validation, cwd/environment restrictions, timeout, CPU/memory limits, output limits, filesystem restrictions, and audit logging.

Prevent traversal, symlink/hardlink escape, cwd escape, and injection.

All forbidden operations fail closed.
