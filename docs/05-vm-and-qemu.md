# 05 — VM and QEMU

## Prompt

Build the minimum secure VM runtime before convenience features.

Each Run gets a fresh VM with immutable base image and disposable writable overlay.

Do not expose host root, Docker socket, SSH agent, cloud credentials, arbitrary host devices, or broad directories unless explicitly authorized.

Images use immutable digests.

Acceptance tests must verify:

- approved image starts;
- overlay is disposable;
- VM is destroyed after Run;
- unauthorized host paths are invisible;
- Runs cannot access one another;
- runner APIs are unreachable unless explicitly allowed.

Hardening backlog: seccomp/AppArmor, minimal devices, signed images, digest verification, scanning, quotas, QEMU process isolation.
