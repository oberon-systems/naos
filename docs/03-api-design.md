# 03 — API Design

## Prompt

Implement the control-plane API as a thin HTTP layer over application/domain services.

## Stack

Python 3.12+, FastAPI, Pydantic, SQLModel, SQLite.

## Responsibilities

Own Agents, Images, Profiles, Policies, Runs, Runners, Leases, audit records, and merge requests.

## Suggested endpoints

```text
POST /api/v1/runs
GET /api/v1/runs
GET /api/v1/runs/{run_id}
POST /api/v1/runs/{run_id}/stop
POST /api/v1/runners/register
POST /api/v1/runners/{runner_id}/heartbeat
GET /api/v1/runners/{runner_id}/runs
GET /api/v1/runs/{run_id}/console
GET /api/v1/runs/{run_id}/events
GET /api/v1/runs/{run_id}/diff
POST /api/v1/runs/{run_id}/merge
POST /api/v1/policy-snapshots
GET /api/v1/policy-snapshots/{snapshot_id}
```

Do not allow clients to arbitrarily set Run status. Validate legal transitions centrally.

Use transactions for atomic transitions and design mutations to be idempotent.

## Idempotency

- `POST /runs` requires an `Idempotency-Key` header. The same key with the same
  spec returns the existing Run with 200; with another spec it returns 409.
- `POST /runs/{run_id}/stop` returns the current Run when there is nothing to
  stop.
- `POST /policy-snapshots` deduplicates by content: the same document returns
  the existing snapshot with 200.
- Transitions are compare-and-swap on the expected status. A repeated
  transition that already happened is a no-op.

## Runner interface

Runners use the same API under `/api/v1/runners`. A runner token is not an
operator principal and opens nothing outside its own runner.

```text
POST /api/v1/runners/register                              enrollment token
POST /api/v1/runners/{runner_id}/heartbeat                 runner token
GET  /api/v1/runners/{runner_id}/runs                      runner token
POST /api/v1/runners/{runner_id}/runs/{run_id}/transition  runner token
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
- `GET .../runs` returns the desired state: every non-terminal Run on the
  live lease, with its spec and the resolved policy snapshot documents.

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
