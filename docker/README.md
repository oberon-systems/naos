# naos stack

[Compose](https://docs.docker.com/compose/) file that brings up the naos
control plane from the published images: the
[api](https://github.com/oberon-systems/naos/blob/main/packages/api/README.md),
the [web ui](https://github.com/oberon-systems/naos/blob/main/packages/web/README.md)
and a [PostgreSQL](https://www.postgresql.org/) database. The same file serves a
local test stack and a real deployment; only `.env` differs between them.

The runner is not part of the stack. It needs KVM, virtiofsd and the host
workspace, so it runs on the host as a user unit and reaches the api over the
published port. `docker/api/Dockerfile` and `docker/web/Dockerfile` build the
two images the compose file pulls.

## Configure

Copy the example and fill it in. Every variable is documented in the file
itself; `NAOS_DB_PASSWORD` and the two token hashes have no default and compose
refuses to start without them.

```bash
cd docker
cp .env.example .env
chmod 600 .env
```

The api authenticates two different callers, so there are two tokens and two
hashes:

| Variable | Whose token | Who presents it | What it closes |
|---|---|---|---|
| `NAOS_OPERATOR_TOKEN_SHA256` | operator token | you, curl or CI, as `Authorization: Bearer` | everything under `/api/v1` except `/api/v1/runners` |
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | enrollment token | a runner once, at `POST /api/v1/runners/register` | registering new runners |

Generate both, keep the tokens somewhere safe and write only their hashes into
`.env`. `printf %s` matters: a trailing newline hashes to something else.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
printf %s TOKEN | sha256sum | cut -d' ' -f1
```

A runner never sees the operator token, and the enrollment token is spent at
registration: from then on the runner uses the runner token the api issued it.
Either hash left unset closes its routes, it does not open them.

For a deployment, also pin `NAOS_TAG` to a released version instead of
`latest`, set `NAOS_ALLOWED_MOUNT_ROOTS` to the directories a Run may mount,
and point `NAOS_WEB_API_BASE_URL` at the api as an operator's browser reaches
it. `NAOS_BIND` stays on `127.0.0.1` unless a reverse proxy terminating TLS
sits in front.

## Run

```bash
docker compose pull
docker compose up -d
docker compose ps
```

To run the images built from this working tree instead of the published ones,
set `NAOS_TAG` in `.env` to a tag of your own, `local` for instance, and build
them:

```bash
make -C docker images
make -C docker up
```

`images` reads the same `.env`, so it tags exactly what compose then starts.
For a local stack with the runner and the tokens already wired up, use
[dev/stack](../dev/stack/README.md) instead.

The api waits for the database to report healthy and creates its schema on
startup. Check both services:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/runs
```

Expected output:

```text
{"status":"ok"}
200
```

The api answers 401 to everything under `/api/v1` without the operator token:

```bash
curl -fsS -H "Authorization: Bearer $NAOS_OPERATOR_TOKEN" \
    http://127.0.0.1:8080/api/v1/runs
```

## Operate

Point the [runner](https://github.com/oberon-systems/naos/blob/main/packages/runner/README.md)
on the host at the published api and give it the file holding the enrollment
token. The runner is an unprivileged user process, so both paths are that
user's own; the token file is 0600 and the state directory is created 0700:

```bash
export NAOS_AGENT_API_URL=http://127.0.0.1:8080
export NAOS_AGENT_NAME=alpha
export NAOS_AGENT_STATE_DIR="$HOME/.local/state/naos/agent"
export NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$HOME/.config/naos/enrollment"
runner
```

Leave `NAOS_AGENT_IMAGE_DIR` and `NAOS_AGENT_VM_DIR` alone unless you want them
elsewhere: they default to `~/.local/share/naos/vms` and
`~/.local/state/naos/runs`.

A registered runner shows up in three places: its own log, the
`credentials.json` the api issued it in the state directory, and a
`runner_registered` record in the audit trail.

```bash
curl -fsS -H "Authorization: Bearer $NAOS_OPERATOR_TOKEN" \
    http://127.0.0.1:8080/api/v1/audit
```

There is no operator route listing runners yet, and the web ui is still a shell
that makes no api calls, so the audit trail is the place to look.

Upgrading is a new tag and a restart; the schema follows the api image:

```bash
docker compose pull
docker compose up -d
```

The directory `NAOS_DB_DATA` names is the only state the stack keeps. Back it
up with the database stopped, or with `pg_dump` while it runs:

```bash
docker compose exec db pg_dump -U naos naos > naos.sql
```

`docker compose down` keeps that directory; `down -v` does not touch it either,
since the data lives in a bind mount rather than a volume.
