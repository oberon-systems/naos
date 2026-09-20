# naos api

The control plane of [naos](https://github.com/oberon-systems/naos). It owns
Runs and the immutable policy each Run is created with, the image catalog, the
profiles and the audit trail, and it is the only thing runners talk to: they
register, lease Runs, report transitions and push events through it.

The service is [FastAPI](https://fastapi.tiangolo.com/) over
[SQLModel](https://sqlmodel.tiangolo.com/), with no state of its own besides
the database. Operator routes live under `/api/v1` behind a bearer token, the
runner interface under `/api/v1/runners` behind a token each runner is issued
at registration.

## Install

```bash
pip install "naos-api[server]"
```

The `server` extra adds [uvicorn](https://www.uvicorn.org/) and the
[psycopg](https://www.psycopg.org/) driver, which the library itself does not
need. The published image carries both:

```bash
docker pull ghcr.io/oberon-systems/naos-api
```

## Configure

Settings are read from `NAOS_*` environment variables once per process. Both
token settings hold a SHA-256, never the token; while one is unset, the routes
behind it answer 401.

| Variable | Default | Meaning |
|---|---|---|
| `NAOS_DATABASE_URL` | required | For example `postgresql+psycopg://naos@db.example.com/naos` |
| `NAOS_ALLOWED_MOUNT_ROOTS` | `[]` | Host directories a mount policy may name |
| `NAOS_OPERATOR_TOKEN_SHA256` | unset | SHA-256 of the operator token |
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | unset | SHA-256 of the enrollment token |
| `NAOS_LEASE_TTL_SECONDS` | `60` | Lease extension per heartbeat, 5 to 3600 |

The full table, the endpoints and the runner interface are in
[docs/03-api-design.md](https://github.com/oberon-systems/naos/blob/main/docs/03-api-design.md).

## Run

```bash
export NAOS_DATABASE_URL=postgresql+psycopg://naos@127.0.0.1/naos
export NAOS_OPERATOR_TOKEN_SHA256=$(printf %s "$NAOS_OPERATOR_TOKEN" | sha256sum | cut -d' ' -f1)
uvicorn --factory naos_api.app:create_app --host 127.0.0.1
```

`create_app()` builds the application and starts the background sweep that
expires runner leases; the schema is created on startup.
