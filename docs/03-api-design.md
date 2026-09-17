# 03 — API Design

## Prompt

Implement the control-plane API as a thin HTTP layer over application/domain services.

## Stack

Python 3.12+, FastAPI, Pydantic, SQLModel. `NAOS_DATABASE_URL` names any
database SQLAlchemy supports. The schema is plain tables with no triggers,
stored procedures or foreign keys, so the same SQL runs everywhere. The task
lifecycle is a [transitions](https://github.com/pytransitions/transitions)
state machine.

## Time and ordering

- Every timestamp, in the database and on the wire, is an integer count of
  seconds since the Unix epoch, UTC.
- Tasks carry a unique, increasing `seq`. Runners receive PENDING tasks in
  `seq` order, and `GET /tasks` lists them newest first.

## Settings

The API reads its settings from `NAOS_*` environment variables once per
process. List values are JSON. Every route depends only on the values it uses.

| Variable | Default | Meaning |
|---|---|---|
| `NAOS_DATABASE_URL` | required | Database URL, for example `postgresql+psycopg://naos@db.example.com/naos` |
| `NAOS_ALLOWED_MOUNT_ROOTS` | `[]` | Host directories a mount policy may name |
| `NAOS_OPERATOR_TOKEN_SHA256` | unset | SHA-256 of the operator token; unset closes every operator route |
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | unset | SHA-256 of the enrollment token; unset closes registration |
| `NAOS_RUNNER_TOKEN_TTL_SECONDS` | `86400` | Runner token lifetime, 60 to 604800 |
| `NAOS_LEASE_TTL_SECONDS` | `60` | Lease extension per heartbeat, 5 to 3600 |
| `NAOS_LEASE_SWEEP_INTERVAL_SECONDS` | `15` | Background lease sweep period, 1 to 3600 |
| `NAOS_RUN_CREDENTIAL_TTL_SECONDS` | `300` | Lifetime of a credential issued to a runner, 60 to 3600 |

## Operator credentials

Every route under `/api/v1` outside `/api/v1/runners` takes the operator
token as a Bearer credential. The API holds only its SHA-256 in
`NAOS_OPERATOR_TOKEN_SHA256`; while that is unset, or the token does not
match, every such route answers 401 before its handler runs.

## Responsibilities

Own Agents, Images, Profiles, Policies, Runs, Runners, Leases, audit records, and merge requests.

## Suggested endpoints

```text
POST /api/v1/tasks
GET /api/v1/tasks
GET /api/v1/tasks/{task_id}
POST /api/v1/tasks/{task_id}/stop
POST /api/v1/runners/register
POST /api/v1/runners/{runner_id}/heartbeat
GET /api/v1/runners/{runner_id}/tasks
GET /api/v1/tasks/{task_id}/console
GET /api/v1/tasks/{task_id}/events
GET /api/v1/tasks/{task_id}/diff
POST /api/v1/tasks/{task_id}/merge
POST /api/v1/policies
GET /api/v1/policies/{policy_id}
POST /api/v1/images
GET /api/v1/images
GET /api/v1/images/{image_id}
POST /api/v1/secrets
GET /api/v1/secrets/{name}
```

Do not allow clients to arbitrarily set Run status. Validate legal transitions centrally.

Use transactions for atomic transitions and design mutations to be idempotent.

## Idempotency

- `POST /tasks` requires an `Idempotency-Key` header. The same key with the same
  spec returns the existing Run with 200; with another spec it returns 409.
- `POST /tasks/{task_id}/stop` returns the current Run when there is nothing to
  stop.
- `POST /policies` deduplicates by content: the same document returns
  the existing policy with 200.
- Transitions are compare-and-swap on the expected status. A repeated
  transition that already happened is a no-op.
- `POST /images` with the same `id`, `version`, `digest` and `url` returns the
  existing image with 200. The same `id` or `digest` with other values
  returns 409.

## Policies

One endpoint creates every kind of policy. `kind` discriminates the body, and
`document` is validated against that kind:

```bash
curl -fsS "$api/api/v1/policies" -d '{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}'
```

The API resolves the document into its canonical form before storing it, so
equivalent documents share one digest and therefore one id. Ids carry the kind:
`mntpol_`, `netpol_`, `shellpol_`, `mcppol_`.

A Run names a policy per kind under `spec.mounts`, `spec.network`, `spec.shell`
and `spec.mcp`, and the reference is immutable once the Run starts. The runner
receives the resolved document as a snapshot rather than the id. The network
document is described in [06](06-network-gate.md), the shell document in
[07](07-shell-gate.md), the MCP document in [08](08-mcp-gate.md).

## Secrets

A secret is a provider credential an MCP policy names by `name`. The API
stores it as given, without encryption for now, and never returns its value.

- `POST /api/v1/secrets` takes `name`, `value` and an optional `expires_at`
  and answers 201 with `id`, `name`, `expires_at` and `created_at`.
- `name` matches `^[a-z0-9][a-z0-9._-]{0,63}$`, `value` is 1 to 8192 visible
  ASCII characters; anything else gets 422.
- A name that already exists gets 409, and `GET /api/v1/secrets/{name}`
  returns the same metadata or 404.

## Images

The API keeps the catalog of images a Run may boot: what to boot and where to
download it from. It stores no image bytes; the runner downloads the file
itself and trusts the digest, not the host. The images are built as described
in [packer/README.md](../packer/README.md).

- An operator registers an image with `POST /api/v1/images` and a body of
  `id`, `version`, `digest` and `url`. The API answers 201 and never contacts
  the url.
- The url must use https and must not carry credentials; any other url gets
  422.
- `id`, `version`, `digest` and `url` are immutable and image rows are never
  deleted: no endpoint or service writes them, like the Run spec.
- `POST /tasks` requires a registered image with the same `id` and `digest`,
  otherwise it returns 422.

## Runner interface

Runners use the same API under `/api/v1/runners`. A runner token is not an
operator principal and opens nothing outside its own runner.

```text
POST /api/v1/runners/register                                enrollment token
POST /api/v1/runners/{runner_id}/heartbeat                   runner token
GET  /api/v1/runners/{runner_id}/tasks                       runner token
POST /api/v1/runners/{runner_id}/tasks/{task_id}/transition  runner token
```

### Runner credentials

- Registration takes the enrollment token as a Bearer credential. The API
  holds only its SHA-256 in `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256`; while that
  is unset, registration is closed.
- Registration returns a random runner token once. The API stores its SHA-256
  and expires it after `NAOS_RUNNER_TOKEN_TTL_SECONDS`, 86400 by default.
- Past half its lifetime a heartbeat returns a replacement. The previous
  token stays valid until its own expiry, and a heartbeat made with it gets a
  fresh replacement, so a lost response never locks the runner out.
- A runner token used on another runner's path gets 403.

### Leases

- A runner holds at most one live lease. A heartbeat extends it by
  `NAOS_LEASE_TTL_SECONDS`, 60 by default; a heartbeat after expiry opens a
  new lease with a new id.
- Expiry is detected on every runner call and by a background sweep every
  `NAOS_LEASE_SWEEP_INTERVAL_SECONDS`. PENDING Runs of the expired lease
  return to the pool; STARTING, STARTED, STOPPING and COLLECTING Runs become
  FAILED with the reason `runner lease expired`.
- A heartbeat assigns unassigned PENDING Runs up to the capacity the runner
  reports. Each assignment is compare-and-swap, so a Run never lands on two
  leases.
- `GET .../tasks` returns the desired state: every non-terminal Run on the
  live lease, with its spec, the `image_url` of its image, the resolved
  policy documents and `credentials`.
- `credentials` maps each secret the MCP policy names to `value` and
  `expires_at`, only for STARTING and STARTED Runs. A missing or expired
  secret is left out, and `expires_at` is at most
  `NAOS_RUN_CREDENTIAL_TTL_SECONDS` away, so a runner that loses its lease
  loses its credentials with it.

### Runner transitions

A runner may only make these transitions; any other pair gets 409.

| From | To |
|---|---|
| PENDING | STARTING |
| STARTING | STARTED |
| STARTED | STOPPING |
| STOPPING | COLLECTING |
| STARTING, STARTED, STOPPING, COLLECTING | FAILED, with a reason |

Every transition names the lease and is compare-and-swap on both the status
and the lease. A stale lease gets 409, a Run held by another runner gets 404,
and repeating a transition that already happened is a no-op.

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
