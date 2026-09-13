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
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | unset | SHA-256 of the enrollment token; unset closes registration |
| `NAOS_RUNNER_TOKEN_TTL_SECONDS` | `86400` | Runner token lifetime, 60 to 604800 |
| `NAOS_LEASE_TTL_SECONDS` | `60` | Lease extension per heartbeat, 5 to 3600 |
| `NAOS_LEASE_SWEEP_INTERVAL_SECONDS` | `15` | Background lease sweep period, 1 to 3600 |
| `NAOS_IMAGE_STORE` | `fs` | Image store backend; `fs` is the only one |
| `NAOS_IMAGE_STORE_PATH` | `$XDG_DATA_HOME/naos/images`, else `~/.local/share/naos/images` | Directory of the `fs` store |
| `NAOS_IMAGE_SOURCE_URL` | unset | Download URL template with `{version}`; unset disables import |
| `NAOS_IMAGE_SOURCE_ALLOWED_HOSTS` | `[]` | Hosts an image download may be redirected to |
| `NAOS_IMAGE_MAX_BYTES` | `8589934592` | Largest image accepted |
| `NAOS_IMAGE_DOWNLOAD_TIMEOUT_SECONDS` | `30` | Connect and read timeout of an image download, 1 to 3600 |

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
- `POST /images` with the same `id`, `version` and `digest` returns the
  current image with 200, or restarts a FAILED import with 202. The same `id`
  or `digest` with other values returns 409.

## Images

The API owns the images a Run may boot; the runner never downloads from
anywhere else. The images are built as described in
[packer/README.md](../packer/README.md).

- An operator registers an image with `POST /api/v1/images` and a body of
  `id`, `version` and `digest`. The API answers 202 with status IMPORTING and
  downloads the file in the background.
- The download URL comes only from `NAOS_IMAGE_SOURCE_URL` with `{version}`
  filled in; a request cannot name a URL. The URL and every redirect must use
  https, and at most five redirects are followed, each only to the source host
  or a host in `NAOS_IMAGE_SOURCE_ALLOWED_HOSTS`.
- The body is hashed while it streams into a temporary file inside the store.
  A digest mismatch, a body over `NAOS_IMAGE_MAX_BYTES`, a non-200 answer or a
  refused redirect makes the image FAILED with a reason and leaves nothing in
  the store. A matching file is renamed to `sha256-<hex>.qcow2`, mode 0444,
  and the image becomes READY.
- An import cut off by an API restart is marked FAILED when the API starts
  again, so it can be retried.
- `id`, `version` and `digest` are immutable and image rows are never deleted:
  no endpoint or service writes them, like the Run spec.
- `POST /tasks` requires the named image to be READY with the same `id` and
  `digest`, otherwise it returns 422.
- The store is an interface. `fs`, one directory with mode 0700, is the only
  backend; object storage can be added behind the same interface.

## Runner interface

Runners use the same API under `/api/v1/runners`. A runner token is not an
operator principal and opens nothing outside its own runner.

```text
POST /api/v1/runners/register                                enrollment token
POST /api/v1/runners/{runner_id}/heartbeat                   runner token
GET  /api/v1/runners/{runner_id}/tasks                       runner token
POST /api/v1/runners/{runner_id}/tasks/{task_id}/transition  runner token
GET  /api/v1/runners/{runner_id}/images/{digest}             runner token
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
  live lease, with its spec and the resolved policy documents.

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

### Runner images

`GET .../images/{digest}` streams a READY image from the store. It answers
only when the digest belongs to a non-terminal Run on the runner's live lease;
any other digest gets 404, the same answer as an image missing from the store.

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
