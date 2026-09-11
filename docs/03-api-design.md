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
```

Do not allow clients to arbitrarily set Run status. Validate legal transitions centrally.

Use transactions for atomic transitions and design mutations to be idempotent.

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
