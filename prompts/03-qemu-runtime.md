# Prompt 03 — QEMU Runtime

Read `AGENTS.md`, `docs/04-runner-design.md`, and `docs/05-vm-and-qemu.md`.

Implement immutable image lookup by digest, disposable overlay creation, QEMU process construction, monitoring, shutdown, and cleanup.

No implicit host paths/devices. No VM reuse between unrelated Runs.

Acceptance: approved image boots, overlay is disposable, cleanup works after success and QEMU failure, unauthorized host files are inaccessible.
