# Prompt 07 — Overlay Diff

Read `AGENTS.md` and `docs/09-overlay-and-merge.md`.

Collect the disposable overlay after VM termination and produce a structured diff for created, modified, deleted, and safely detectable renamed files.

Treat overlay contents as untrusted. Never follow an object in a way that escapes the authorized workspace.

Do not apply changes to the host yet.

Acceptance: deterministic diff and security tests for malicious filesystem objects.
