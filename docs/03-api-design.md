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

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
