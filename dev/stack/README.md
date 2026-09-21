# Local stack

Naos on one machine in a single command: the
[compose stack](../../docker/README.md) - api, web ui and the database - from
images built out of this working tree, plus the
[runner](../../packages/runner/README.md) as your own user. Everything it
creates stays inside `dev/stack/local/`, so nothing touches `/etc` or
`/var/lib` and the whole setup is thrown away with `make clean`.

- [Kickstart](#kickstart)
- [Commands](#commands)
- [What it writes](#what-it-writes)
- [See the agent](#see-the-agent)

## Kickstart

The host needs [docker](https://docs.docker.com/engine/install/) with the
compose plugin, a Rust toolchain for the runner, and `make install` already run
in the repository root, because the tokens are generated with the virtualenv's
Python.

```bash
make kickstart
```

The first run writes both tokens, fills `docker/.env` from `.env.example` with
their hashes and `NAOS_TAG=local`, builds the two images and `naos-runner`,
starts compose, waits for `/healthz` and starts the runner in the background.
Every later run reuses the same tokens and skips whatever is already up.

Every run builds all three from this tree, before anything starts. Docker caches
its layers and cargo its crates, so an unchanged tree costs a moment, and the
stack never runs a binary older than the code in front of you.

An existing `docker/.env` is never rewritten. Kickstart compares the two hashes
in it against the tokens in `local/` and stops when they differ, so a stack you
configured by hand keeps its own credentials: put those tokens into
`local/operator` and `local/enrollment`, or start over with `make clean`.

The one key it does add is `NAOS_WEB_OPERATOR_TOKEN_FILE`, and only when the
`.env` has no value for it, so an `.env` from before the web ui read the API
keeps every line you edited. `local/operator` is 0644 because the web container
reads it as `nobody`; `local/` is 0700, and that is what keeps it off other
accounts on this machine.

For the same reason it refuses to write a new `docker/.env` while
`docker/data/db` still holds a cluster: postgres keeps the password of its first
start, and a generated one would never match it. `make -C dev/stack clean`
empties both, or bring back the `.env` that cluster was created with.

Nothing is left half up: when the api does not answer within five seconds or
the runner does not enroll, kickstart prints the api log, stops the stack and
the runner, and exits.

All settings come from `docker/.env`, the same file compose reads, so the
printed urls follow `NAOS_BIND`, `NAOS_API_PORT` and `NAOS_WEB_PORT`.

## Commands

| Target                          | What it does                                |
| ------------------------------- | ------------------------------------------- |
| `make kickstart`                | Tokens, images, compose and the runner      |
| `make -C dev/stack runner`      | Run the runner in the foreground            |
| `make -C dev/stack down`        | Stop everything and empty the database      |
| `make -C dev/stack clean`       | `down`, then delete `local/` and `.env`     |

`runner` is the same agent with the same environment, attached to the terminal:
use it when you want its output live instead of tailing the log. Stop the
background one with `down` first.

`down` stops the runner and compose and then empties the database from inside a
container, because the cluster belongs to root: `docker/data/db` stays as an
empty directory. Tokens and `docker/.env` are left alone, so the next
`kickstart` comes back on the same credentials and a schema the api creates
again.

`clean` is `down` plus the tokens, the agent state and `docker/.env`, which
leaves nothing behind for the next `kickstart` to reuse.

## What it writes

| Path                              | What it is                                  |
| --------------------------------- | ------------------------------------------- |
| `local/operator`                  | Operator token, mode 0600                   |
| `local/enrollment`                | Enrollment token the runner reads, 0600     |
| `local/agent/`                    | `NAOS_AGENT_STATE_DIR`: `credentials.json`  |
| `local/agent.log`, `agent.pid`    | Output and pid of the background runner     |
| `docker/.env`                     | Compose settings, written once, mode 0600   |

The image cache and the VM directories are not set, so the runner uses its XDG
defaults, `~/.local/share/naos/vms` and `~/.local/state/naos/runs`.

## See the agent

The runner registers once and then heartbeats. Three things say it worked, the
last one against the api url kickstart printed:

```bash
tail dev/stack/local/agent.log
cat dev/stack/local/agent/credentials.json
curl -fsS -H "Authorization: Bearer $(cat dev/stack/local/operator)" \
    http://127.0.0.1:8080/api/v1/audit
```

The audit trail holds a `runner_registered` record with the runner id. There is
no operator route listing runners yet, and the web ui is still a shell that
makes no api calls, so the audit trail is where an enrolled runner shows up.
