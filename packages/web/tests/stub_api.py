"""The api the runs page reads, answering the states the board draws.

Every row here exists to put one state, one action or one runner condition on the
page; the shapes are the ones packages/api returns.
"""

import time
from typing import Any

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse, Response

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
    policies: Row | None = None,
    extra: Row | None = None,
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
            "runner": None,
        }
        | (policies or {}),
        "profile_id": None,
        "lease_id": None,
        "workspace": workspace,
        "runner": runner,
        "merge": merge,
        "created_at": created or NOW - 120,
        "updated_at": NOW,
        "started_at": started,
        "finished_at": finished,
    } | (extra or {})


NETPOL = "netpol_9a07" + "0" * 28
MCPPOL = "mcppol_5b2d" + "0" * 28
ALPHA = {"id": "rnr_8c1f42aa", "name": "alpha"}
BETA = {"id": "rnr_4ad907bb", "name": "beta"}
GAMMA = {"id": "rnr_2e77b0cc", "name": "gamma"}

RUNS: list[Row] = [
    run(
        128,
        "run_9f21c4",
        "STARTED",
        runner=ALPHA,
        started=NOW - 134,
        created=NOW - 140,
        policies={"network": {"policy": NETPOL}, "mcp": {"policy": MCPPOL}},
        extra={"profile_id": "prof_7a1c30", "lease_id": "lease_5d2a91"},
    ),
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


@stub.get("/api/v1/audit")
def audit(runner_id: str | None = None, limit: int = Query(100)) -> list[Row]:
    rows: list[Row] = [
        {
            "seq": 5,
            "id": "ev_5",
            "at": NOW - 120,
            "source": "api",
            "event": "run_claimed",
            "actor": "runner",
            "run_id": "run_9f21c4",
            "vm_id": None,
            "runner_id": runner_id,
            "data": {"run_id": "run_9f21c4"},
        },
        {
            "seq": 4,
            "id": "ev_4",
            "at": NOW - 720,
            "source": "api",
            "event": "token_rotated",
            "actor": "runner",
            "run_id": None,
            "vm_id": None,
            "runner_id": runner_id,
            "data": {},
        },
        {
            "seq": 3,
            "id": "ev_3",
            "at": NOW - 2460,
            "source": "api",
            "event": "lease_acquired",
            "actor": "runner",
            "run_id": None,
            "vm_id": None,
            "runner_id": runner_id,
            "data": {"lease_id": "lease_5d2a91"},
        },
    ]
    return rows[:limit]


# The transitions POST /runs/{id}/stop makes; any other state answers unchanged.
STOPS = {
    "PENDING": ("CANCELLED", "cancelled while pending"),
    "STARTING": ("STOPPING", "stop requested by operator"),
    "STARTED": ("STOPPING", "stop requested by operator"),
}


@stub.post("/api/v1/runs/{run_id}/stop")
def stop(run_id: str) -> Row:
    WRITES.append(("POST", f"/runs/{run_id}/stop", {}, None))
    for row in RUNS:
        if row["id"] == run_id and row["status"] in STOPS:
            row["status"], row["status_reason"] = STOPS[row["status"]]
            row["finished_at"] = NOW if row["status"] == "CANCELLED" else None
            return row
    return RUNS[0]


def profile(pid: str, name: str, cpu: int, memory: int, active: Row | None, used: bool) -> Row:
    return {
        "id": pid,
        "name": name,
        "spec": {
            "runtime": {"cpu": cpu, "memory_mib": memory, "disk_gib": 20},
            "mounts": {"policy": None},
            "network": {"policy": NETPOL},
            "shell": {"policy": None},
            "mcp": {"policy": None},
            "merge": {"policy": "ask"},
            "timeout": 3600,
        },
        "active_runs": 1 if active else 0,
        "active_run": active,
        "last_run_at": NOW - 600 if used else None,
        "created_at": NOW - 9000,
        "updated_at": NOW - 9000,
    }


PROFILES: list[Row] = [
    profile(
        "prof_7a1c30",
        "build-small",
        2,
        4096,
        {"id": "run_9f21c4", "seq": 128, "status": "STARTED"},
        True,
    ),
    profile("prof_a93e07", "docs", 1, 2048, None, True),
    profile("prof_91ba35", "sandbox", 4, 8192, None, False),
]
POLICIES: list[Row] = [
    {
        "id": NETPOL,
        "kind": "network",
        "digest": "0" * 64,
        "document": {
            "allow": [
                {"protocol": "https", "host": f"{name}.example.com"}
                for name in ("alpha", "beta", "gamma")
            ],
            "deny": [],
        },
        "created_at": NOW - 9000,
    },
    {
        "id": "mntpol_4c1e" + "0" * 28,
        "kind": "mount",
        "digest": "1" * 64,
        "document": {
            "workdir": "/naos/api",
            "mounts": [
                {"host_path": "/srv/projects/alpha/api", "guest_path": "/naos/api", "mode": "rw"}
            ],
        },
        "created_at": NOW - 9000,
    },
    {
        "id": MCPPOL,
        "kind": "mcp",
        "digest": "2" * 64,
        "document": {
            "servers": [
                {
                    "name": "alpha",
                    "url": "https://mcp.example.com/mcp",
                    "tools": [],
                    "resources": [],
                    "credential": "alpha-token",
                    "timeout_seconds": 30,
                    "max_calls_per_minute": 60,
                }
            ]
        },
        "created_at": NOW - 9000,
    },
]
IMAGES: list[Row] = [
    {
        "id": "naos-agents",
        "version": "1.4.2",
        "digest": "sha256:3f9a" + "0" * 56 + "c21e",
        "url": "https://images.example.com/a.qcow2",
        "created_at": NOW - 100,
    },
    {
        "id": "naos-agents-old",
        "version": "1.3.0",
        "digest": "sha256:" + "b" * 64,
        "url": "https://images.example.com/b.qcow2",
        "created_at": NOW - 9000,
    },
]
# Every write the dialog sends, in order, so a test reads what reached the api.
WRITES: list[tuple[str, str, Row, str | None]] = []


def _found(pid: str) -> Row:
    return next(row for row in PROFILES if row["id"] == pid)


@stub.get("/api/v1/profiles")
def list_profiles(q: str | None = None) -> list[Row]:
    needle = (q or "").lower()
    return [row for row in PROFILES if needle in row["name"].lower() or needle in row["id"]]


@stub.get("/api/v1/profiles/{pid}")
def get_profile(pid: str) -> Row:
    return _found(pid)


@stub.post("/api/v1/profiles")
def create_profile(body: Row) -> Row:
    WRITES.append(("POST", "/profiles", body, None))
    return profile("prof_new001", body["name"], 1, 1024, None, False) | {"spec": body["spec"]}


@stub.put("/api/v1/profiles/{pid}", response_model=None)
def update_profile(pid: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("PUT", f"/profiles/{pid}", body, None))
    found = _found(pid)
    if found["active_run"]:
        detail = f"profile {found['name']} cannot change: #128 is STARTED on it"
        return JSONResponse({"detail": detail}, 409)
    return found | {"spec": body["spec"]}


@stub.post("/api/v1/profiles/{pid}/runs")
def run_from_profile(pid: str, body: Row, idempotency_key: str = Header()) -> Row:
    WRITES.append(("POST", f"/profiles/{pid}/runs", body, idempotency_key))
    return RUNS[0]


@stub.get("/api/v1/policies")
def list_policies() -> list[Row]:
    return POLICIES


@stub.get("/api/v1/images")
def list_images() -> list[Row]:
    return IMAGES


def _event(
    seq: int,
    at: int,
    event: str,
    data: Row,
    source: str = "api",
    actor: str = "system",
    vm_id: str | None = None,
) -> Row:
    return {
        "seq": seq,
        "id": f"evt_{seq:032x}",
        "at": at,
        "source": source,
        "event": event,
        "actor": actor,
        "run_id": "run_9f21c4",
        "vm_id": vm_id,
        "runner_id": None if actor == "operator" else ALPHA["id"],
        "data": data,
    }


def _runner(seq: int, at: int, event: str, data: Row, vm_id: str | None = None) -> Row:
    return _event(seq, at, event, data, source="runner", actor="runner", vm_id=vm_id)


# The timeline of run_9f21c4, closed by two rows no writer may produce.
RUN_EVENTS: list[Row] = [
    _event(
        1,
        NOW - 140,
        "run_created",
        {"workspace": "alpha", "profile": "build-small"},
        actor="operator",
    ),
    _event(2, NOW - 138, "run_assigned", {"lease_id": "lease_5d2a91", "slot": 1, "slots": 2}),
    _event(
        3,
        NOW - 134,
        "run_transition",
        {"from": "PENDING", "to": "STARTING", "reason": None},
        actor="runner",
    ),
    _runner(4, NOW - 134, "run_claimed", {}),
    _event(
        5, NOW - 133, "credentials_issued", {"names": ["alpha-token"], "ttl": 2700}, actor="runner"
    ),
    _runner(6, NOW - 130, "vm_created", {}, vm_id="vm_7c1e"),
    _event(
        7,
        NOW - 120,
        "run_transition",
        {"from": "STARTING", "to": "STARTED", "reason": None},
        actor="runner",
    ),
    _runner(
        8,
        NOW - 60,
        "network_denied",
        {
            "protocol": "https",
            "host": "private.example.com",
            "rule": "deny",
            "reason": "denied by policy",
        },
    ),
    _runner(9, NOW - 50, "vm_exploded", {"note": "rm -rf /"}),
    _runner(10, NOW - 40, "run_claimed", {"token": "secret-alpha-value"}),
]


def _missing(what: str) -> JSONResponse:
    return JSONResponse({"detail": f"{what} not found"}, 404)


@stub.get("/api/v1/runs/{run_id}", response_model=None)
def get_run(run_id: str) -> Row | JSONResponse:
    found = next((row for row in RUNS if row["id"] == run_id), None)
    return found if found else _missing(f"run {run_id}")


@stub.get("/api/v1/runs/{run_id}/events")
def run_events(run_id: str) -> list[Row]:
    return RUN_EVENTS if run_id == "run_9f21c4" else []


CONSOLE = b"login: naos\r\n$ pytest -q\r\n"


@stub.get("/api/v1/runs/{run_id}/console", response_model=None)
def console_log(run_id: str) -> Response:
    if not any(row["id"] == run_id for row in RUNS):
        return _missing(f"run {run_id}")
    return Response(CONSOLE, media_type="text/plain")


@stub.get("/api/v1/policies/{policy_id}", response_model=None)
def get_policy(policy_id: str) -> Row | JSONResponse:
    found = next((row for row in POLICIES if row["id"] == policy_id), None)
    return found if found else _missing(f"policy {policy_id}")


@stub.post("/api/v1/runs")
def create_run(body: Row, idempotency_key: str = Header()) -> Row:
    WRITES.append(("POST", "/runs", body, idempotency_key))
    return run(129, "run_a0c311", "PENDING", created=NOW)
