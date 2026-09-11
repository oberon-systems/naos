# 02 — Threat Model

## Prompt

Review every feature against an attacker who controls the agent process.

## Threats

### VM escape

Malicious code escapes QEMU and reaches the host.
Mitigate with isolated VMs, minimal images, hardened QEMU/host, and escape tests.

### Filesystem escape

Threats: `..`, symlinks, hardlinks, absolute paths, special files, mount tricks, TOCTOU.
Mitigate with descriptor-aware path enforcement and constrained mounts.

### Network bypass

Threats: direct IP, IPv6, localhost, private ranges, metadata endpoints, DNS rebinding, redirects, CONNECT, proxy bypass.
Mitigate by enforcing policy at connection establishment.

### Shell abuse

Threats: command/argument injection, environment manipulation, cwd escape, executable substitution, resource exhaustion.
Prefer structured capabilities.

### MCP confused deputy

Threat: permitted integration is abused for unintended privileged actions.
Mitigate with per-Run server/method/resource policy and credential isolation.

### Workspace poisoning

Threat: changes to CI workflows, Git hooks, package scripts, Makefiles, Dockerfiles, deployment config trigger later privileged behavior.
Mitigate with reviewable merge.

### Runner compromise

Threat: VM targets runner APIs.
Mitigate with authenticated narrow interfaces and least privilege.

### Resource exhaustion

Mitigate with CPU, memory, disk, process, network, log, and time limits.

Every new capability must document attacker, asset, attack path, enforcement point, failure behavior, and test.
