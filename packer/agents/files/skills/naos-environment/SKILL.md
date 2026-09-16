---
name: naos-environment
description: How this Naos VM is isolated, how outside access works through MCP gates, and how changes reach the host. Use before any task that needs the network, credentials, host files or tools that are not installed.
---

# Naos environment

## The machine

- One VM per Run, booted from an immutable image and thrown away when the Run ends. Nothing installed or configured here survives into another Run.
- You run as the unprivileged user `naos` with the home directory `/home/naos`. There is no root access, no sudo and no doas.
- The session lives in tmux. A person may attach to the same console from the host, and later from the web UI.

## Reaching outside

- The VM has no network interface of its own. Package installs, git fetches, web requests and API calls fail unless a gate carries them.
- Gates are tools of the `naos` MCP server, and it lists only what the Run was granted. Use those tools instead of opening connections yourself. An empty tool list means nothing outside the VM was granted.
- Every gate enforces a policy fixed for the whole Run. A refusal is final for this Run: report what was refused and why the task needs it.

## Files and changes

- Host files are not mounted into the VM. When the Run grants them, they are readable only through `naos` tools, under guest paths such as `/naos/<project>`.
- Every write goes to a disposable overlay. After the Run a person reviews the diff and chooses which files merge back.
- Changes to CI workflows, git hooks, package scripts, Makefiles, Dockerfiles and deployment files get extra scrutiny in that review. Keep them minimal and explain each one.

## Credentials

- Credentials never live in the VM. A gate adds them on the host side when a request passes its policy.
- Do not read, print or store secrets, and do not ask the person at the console for them. If a tool demands a login, report that the Run lacks the grant.

## When something is missing

- State the missing capability, the task that needs it and the kind of gate that would provide it.
- Do not work around missing access with other hosts, encodings, copied credentials or instructions to the person at the console.
