"""The api the runs page reads, answering the states the board draws.

Every row here exists to put one state, one action or one runner condition on the
page; the shapes are the ones packages/api returns.
"""

import time
from typing import Any

from fastapi import FastAPI, Query

NOW = int(time.time())
Row = dict[str, Any]


def run(
    seq: int,
    rid: str,
    status: str,
    reason: str | None = None,
    workspace: str = "alpha",
    runner: Row | None = None,
    started: int | None = None,
    finished: int | None = None,
    merge: Row | None = None,
    created: int | None = None,
) -> Row:
    return {
        "id": rid,
        "seq": seq,
        "status": status,
        "status_reason": reason,
        "spec": {
            "image": {"id": "naos-agents", "digest": "sha256:" + "ab12cd34ef56" + "0" * 52},
            "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 10},
            "mounts": {"policy": None},
            "network": {"policy": None},
            "shell": {"policy": None},
            "mcp": {"policy": None},
            "merge": {"policy": "ask"},
            "timeout": 3600,
        },
        "workspace": workspace,
        "runner": runner,
        "merge": merge,
        "created_at": created or NOW - 120,
        "updated_at": NOW,
        "started_at": started,
        "finished_at": finished,
    }


ALPHA = {"id": "rnr_8c1f42aa", "name": "alpha"}
BETA = {"id": "rnr_4ad907bb", "name": "beta"}
GAMMA = {"id": "rnr_2e77b0cc", "name": "gamma"}

RUNS: list[Row] = [
    run(128, "run_9f21c4", "STARTED", runner=ALPHA, started=NOW - 134, created=NOW - 140),
    run(127, "run_7c08ab", "COLLECTING", runner=BETA, started=NOW - 348, created=NOW - 360),
    run(
        126,
        "run_5be317",
        "WAITING_MERGE",
        runner=GAMMA,
        started=NOW - 900,
        finished=None,
        merge={"changed": 12, "conflicts": 0},
        created=NOW - 660,
    ),
    run(125, "run_41d70e", "STARTING", runner=GAMMA, started=NOW - 12, created=NOW - 30),
    run(124, "run_3a90f8", "PENDING", reason=None, runner=None, created=NOW - 60),
    run(
        123,
        "run_2f55d1",
        "STOPPING",
        reason="stop requested by operator",
        runner=ALPHA,
        started=NOW - 800,
        created=NOW - 840,
    ),
    run(
        122,
        "run_1e0c6b",
        "FAILED",
        reason="guest exited 1",
        runner=BETA,
        started=NOW - 1320,
        finished=NOW - 1224,
        created=NOW - 1330,
    ),
    run(
        121,
        "run_0d4492",
        "COMPLETED",
        runner=ALPHA,
        started=NOW - 2280,
        finished=NOW - 1869,
        merge={"changed": 4, "conflicts": 0},
        created=NOW - 2300,
    ),
    run(
        120,
        "run_0b31a7",
        "CANCELLED",
        reason="cancelled while pending",
        runner=None,
        started=None,
        finished=NOW - 3000,
        created=NOW - 3060,
    ),
]

RUNNERS: list[Row] = [
    {
        "id": "rnr_8c1f42aa",
        "name": "alpha",
        "status": "live",
        "capacity": 2,
        "runs": [
            {"id": "run_2f55d1", "seq": 123, "status": "STOPPING"},
            {"id": "run_9f21c4", "seq": 128, "status": "STARTED"},
        ],
        "created_at": NOW - 9000,
        "last_heartbeat_at": NOW - 2,
        "lease_acquired_at": NOW - 8,
        "lease_expires_at": NOW + 52,
        "revoked_at": None,
    },
    {
        "id": "rnr_4ad907bb",
        "name": "beta",
        "status": "live",
        "capacity": 2,
        "runs": [{"id": "run_7c08ab", "seq": 127, "status": "COLLECTING"}],
        "created_at": NOW - 8000,
        "last_heartbeat_at": NOW - 4,
        "lease_acquired_at": NOW - 13,
        "lease_expires_at": NOW + 47,
        "revoked_at": None,
    },
    {
        "id": "rnr_2e77b0cc",
        "name": "gamma",
        "status": "live",
        "capacity": 2,
        "runs": [
            {"id": "run_41d70e", "seq": 125, "status": "STARTING"},
            {"id": "run_5be317", "seq": 126, "status": "WAITING_MERGE"},
        ],
        "created_at": NOW - 7000,
        "last_heartbeat_at": NOW - 1,
        "lease_acquired_at": NOW - 2,
        "lease_expires_at": NOW + 58,
        "revoked_at": None,
    },
    {
        "id": "rnr_91ba35dd",
        "name": "delta",
        "status": "stale",
        "capacity": 2,
        "runs": [],
        "created_at": NOW - 6000,
        "last_heartbeat_at": NOW - 94,
        "lease_acquired_at": None,
        "lease_expires_at": None,
        "revoked_at": None,
    },
    {
        "id": "rnr_6f0d1811",
        "name": "epsilon",
        "status": "revoked",
        "capacity": None,
        "runs": [],
        "created_at": NOW - 5000,
        "last_heartbeat_at": None,
        "lease_acquired_at": None,
        "lease_expires_at": None,
        "revoked_at": NOW - 7200,
    },
]

STATES = {
    "active": {"STARTING", "STARTED", "STOPPING", "COLLECTING"},
    "queued": {"PENDING"},
    "waiting_merge": {"WAITING_MERGE"},
    "failed": {"FAILED"},
}

stub = FastAPI()
RUN_SEQS = [row["seq"] for row in RUNS]


@stub.get("/api/v1/runs")
def list_runs(state: str | None = None, limit: int = Query(50)) -> list[Row]:
    rows = RUNS if state is None else [r for r in RUNS if r["status"] in STATES[state]]
    return rows[:limit]


@stub.get("/api/v1/runs/summary")
def summary() -> Row:
    counts = {
        s: sum(1 for r in RUNS if r["status"] == s)
        for s in [
            "PENDING",
            "STARTING",
            "STARTED",
            "STOPPING",
            "COLLECTING",
            "WAITING_MERGE",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        ]
    }
    return {
        "counts": counts,
        "open": 6,
        "oldest_pending_at": NOW - 180,
        "failed_24h": 1,
        "last_failure_reason": "guest exited 1",
    }


@stub.get("/api/v1/runners")
def list_runners() -> list[Row]:
    return RUNNERS


@stub.post("/api/v1/runs/{run_id}/stop")
def stop(run_id: str) -> Row:
    for row in RUNS:
        if row["id"] == run_id:
            row["status"] = "CANCELLED"
            row["status_reason"] = "cancelled while pending"
            row["finished_at"] = NOW
            return row
    return RUNS[0]
