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

## Everything reads

No capability writes. The gate never creates, changes or removes anything on
the host, and there is no write capability to add one later without a new
policy kind. The only writable layer of a Run is its qcow2 overlay inside the
VM, and it reaches the host through merge ([09](09-overlay-and-merge.md)). The
`mode` of a mount is therefore not the gate's business: a `rw` mount is read
through the gate exactly like a `ro` one.

## Policy

A shell policy grants capabilities and nothing else:

```bash
curl -fsS "$api/api/v1/policies" -d '{"kind": "shell", "document": {"allow": ["read_file", "list_dir", "grep"]}}'
```

`allow` holds any of `read_file`, `list_dir`, `grep`, `git_status` and
`git_diff`. An empty list is refused, a repeated capability is refused, and an
unknown name never validates. The API stores the list in a fixed order, so two
documents granting the same set share one digest and therefore one `shellpol_`
id.

Limits are not part of the document. They are constants of the gate, the same
way the network gate owns its own ([06](06-network-gate.md)).

## Roots

The gate serves the host paths the mount policy named. It reads the resolved
mount snapshot, pairs each `guest_path` with its `host_path`, canonicalizes the
host side once at construction and refuses a root that is missing or is not a
directory. The longest matching guest prefix wins, so a mount nested inside
another is not shadowed by its parent.

A Run carrying a mount policy now starts, which it did not before. The VM still
has no `-virtfs` or `-fsdev` device ([05](05-vm-and-qemu.md)): those host paths
are reachable only through this gate, read-only, and not as a filesystem the
guest can see.

## Enforcement point

The single surface is `ShellGate::call` in
`packages/runner/src/libs/shell/`. The runner builds the gate in
`Runtime::ensure`, before the image is fetched, so a policy or a mount snapshot
it cannot parse fails the Run instead of starting a VM. The gate is registered
against the Run id alongside the network gate and dropped when the VM is
destroyed.

`call` spends a call from the budget, checks the capability was granted,
confines the path, and only then performs the operation. There is no way to
reach a capability without those three steps, and no path value survives them
unresolved.

## Path confinement

Every path an agent submits is a guest path. Confinement walks this order and
denies at the first failure:

| Step | Denied when |
|---|---|
| shape | not absolute, not normalized, over 4096 bytes, or carrying a control character |
| mount | no guest root is a prefix of it |
| resolution | the host path behind it does not exist |
| containment | the resolved path is not under its canonical root |
| kind | it is neither a regular file nor a directory |
| hard link | it is a regular file with more than one link |

The shape check is the same one the API applies to a mount host path in
`packages/api/src/naos_api/mounts.py`, so `..`, `.` and empty segments are gone
before anything touches the filesystem. Containment is checked after
canonicalization, which is what closes a symlink escape: the link is followed
first and the result still has to sit under the root. A hard link cannot be
told from its target by path, so a file carrying an extra link is refused
outright - including the original.

Directory walks never follow a symlink. `list_dir` reports one as `other` and
does not say where it points; `grep` skips it.

## Capabilities

| Capability | Returns |
|---|---|
| `read_file` | the bytes of one regular file |
| `list_dir` | the entries of one directory, sorted, each with kind and size |
| `grep` | literal matches, each with its guest path, line number and line |
| `git_status` | `git status --porcelain=v1` of the worktree |
| `git_diff` | the unstaged diff of the worktree |

`grep` matches a literal substring and runs in process. No pattern reaches a
regular expression engine or an external binary, so neither injection nor a
pathological pattern is reachable through it.

## git without a shell

`git_status` and `git_diff` are the only capabilities that execute anything.
The argv is fixed, the binary is the configured one, and no shell is involved:

```text
git --no-optional-locks --no-pager -C <root> -c core.fsmonitor= status --porcelain=v1
git --no-optional-locks --no-pager -C <root> -c core.fsmonitor= diff --no-color --no-ext-diff --no-textconv
```

The environment is cleared down to `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_OPTIONAL_LOCKS=0` and
`GIT_TERMINAL_PROMPT=0`. There is no `PATH` and no `HOME`.

The reason for each of those flags is that `.git/config` inside the workspace
belongs to the agent, and `diff.external`, textconv filters and
`core.fsmonitor` are arbitrary program execution read out of a config file.
`--no-ext-diff`, `--no-textconv` and `-c core.fsmonitor=` switch them off
rather than trust them to be absent. `--no-optional-locks` keeps git from
writing into `.git`, which is what the read-only rule above requires, and a
test fails if anything under `.git` changes across a status and a diff.

A repository git refuses to open - a foreign owner, a missing `.git` - fails
the call. Nothing relaxes that check.

The binary comes from `NAOS_AGENT_GIT_BINARY`, default `/usr/bin/git`,
validated like the QEMU paths ([04](04-runner-design.md)).

## Limits

| Limit | Value |
|---|---|
| path length | 4096 bytes |
| file read | 4 MiB |
| directory entries | 1000 |
| grep matches | 200 |
| grep line length | 4096 bytes |
| grep files searched | 5000 |
| git timeout | 10 seconds |
| git output | 1 MiB |
| calls per Run | 600 per 60 second window |

A file over the read limit is refused rather than truncated, because a
truncated file reads as a whole one. A capped match list is different: a short
list of matches is honest output, so `grep` returns what it found. A longer
grep line is skipped. Git output is capped while it is read, and a git over
its timeout or its output limit is killed.

## Failure behavior

Every failure denies. An unparseable shell policy or mount snapshot fails the
Run before it starts; an unusable path, an exhausted budget, an oversized file,
a timed-out or failing git, and output the gate cannot decode all fail the
single call. A denial reason never carries the host path or a subprocess's
output, so nothing about the host's layout leaks through it.

## Audit

The gate writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `shell_policy_configured` | `run_id` |
| `shell_allowed` | `run_id`, `capability`, `path` |
| `shell_denied` | `run_id`, `capability`, `path`, `reason` |

`path` is the guest path the agent asked for. The host path behind it is the
host's own layout and is never logged.

## Acceptance

Acceptance tests must verify:

- every capability is authorized before it runs;
- traversal, symlink escape and hard link escape are refused;
- a path outside every mount is refused;
- a capability that was not granted is refused on a legal path;
- every limit in the table above holds;
- a hostile repository config cannot hook an external program;
- git leaves `.git` untouched.

There is no cwd to escape: the gate keeps no working directory, and every path
the agent sends must be an absolute, normalized guest path.

The gate tests in `packages/runner/src/libs/shell/tests.rs` cover all of these,
and `packages/api/tests/test_shell.py` covers policy resolution. The smoke test
calls the gate from inside the guest through the MCP broker: reading a
workspace file answers with the text the host has, while a path outside every
mount and a capability the policy does not grant are refused, so it fails
unless `shell_allowed` and `shell_denied` both reach the agent log:

```bash
make test
make smoke
```
