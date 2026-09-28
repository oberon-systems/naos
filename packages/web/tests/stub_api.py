"""The api the runs page reads, answering the states the board draws.

Every row here exists to put one state, one action or one runner condition on the
page; the shapes are the ones packages/api returns.
"""

import time
from typing import Annotated, Any

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
MNTPOL = "mntpol_4c1e" + "0" * 28
SHELLPOL = "shellpol_c42f" + "0" * 26
PREFIXES = {"mount": "mntpol", "network": "netpol", "shell": "shellpol", "mcp": "mcppol"}
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
        "lease_id": "lease_5d2a91",
        "lease_lapsed_at": None,
        "token_expires_at": NOW + 42120 + 43200,
        "token_rotates_at": NOW + 42120,
        "prev_token_expires_at": NOW + 14400,
        "drained_at": None,
        "heartbeat_seconds": 20,
        "placement": {
            "host": "alpha-01.example.com",
            "address": "192.0.2.11",
            "zone": "zone-a",
            "platform": "linux/amd64 \u00b7 Ubuntu 24.04",
            "version": "0.1.0",
            "labels": ["ci", "amd64"],
        },
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
        "lease_id": "lease_0b77ce",
        "lease_lapsed_at": None,
        "token_expires_at": NOW + 21600 + 43200,
        "token_rotates_at": NOW + 21600,
        "prev_token_expires_at": None,
        "drained_at": None,
        "heartbeat_seconds": None,
        "placement": {
            "host": None,
            "address": None,
            "zone": None,
            "platform": None,
            "version": None,
            "labels": [],
        },
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
        "lease_id": "lease_a10f34",
        "lease_lapsed_at": None,
        "token_expires_at": NOW + 7200 + 43200,
        "token_rotates_at": NOW + 7200,
        "prev_token_expires_at": None,
        "drained_at": None,
        "heartbeat_seconds": None,
        "placement": {
            "host": None,
            "address": None,
            "zone": None,
            "platform": None,
            "version": None,
            "labels": [],
        },
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
        "lease_id": "lease_7fe201",
        "lease_lapsed_at": NOW - 34,
        "token_expires_at": NOW + 75600,
        "token_rotates_at": NOW + 32400,
        "prev_token_expires_at": None,
        "drained_at": None,
        "heartbeat_seconds": None,
        "placement": {
            "host": None,
            "address": None,
            "zone": None,
            "platform": None,
            "version": None,
            "labels": [],
        },
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
        "lease_id": "lease_c3e9a0",
        "lease_lapsed_at": NOW - 7200,
        "token_expires_at": NOW + 3600,
        "token_rotates_at": NOW - 39600,
        "prev_token_expires_at": None,
        "drained_at": None,
        "heartbeat_seconds": None,
        "placement": {
            "host": None,
            "address": None,
            "zone": None,
            "platform": None,
            "version": None,
            "labels": [],
        },
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
def list_runs(
    state: str | None = None,
    runner: str | None = None,
    image: str | None = None,
    profile: str | None = None,
    policy: str | None = None,
    limit: int = Query(50),
) -> list[Row]:
    rows = RUNS if state is None else [r for r in RUNS if r["status"] in STATES[state]]
    if profile is not None:
        rows = [r for r in rows if r["profile_id"] == profile]
    if policy is not None:
        kinds = ("mounts", "network", "shell", "mcp")
        rows = [r for r in rows if policy in (r["spec"][kind]["policy"] for kind in kinds)]
    if runner is not None:
        rows = [r for r in rows if r["runner"] and r["runner"]["id"] == runner]
    if image is not None:
        rows = [r for r in rows if r["spec"]["image"]["id"] == image]
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
def list_runners(limit: int = Query(50)) -> list[Row]:
    return RUNNERS[:limit]


def _runner_row(runner_id: str) -> Row | None:
    return next((row for row in RUNNERS if row["id"] == runner_id), None)


@stub.post("/api/v1/runners/{runner_id}/revoke", response_model=None)
def revoke_runner(runner_id: str) -> Row | JSONResponse:
    WRITES.append(("POST", f"/runners/{runner_id}/revoke", {}, None))
    row = _runner_row(runner_id)
    if row is None:
        return JSONResponse({"detail": f"runner {runner_id} does not exist"}, status_code=404)
    if row["revoked_at"] is None:
        row |= {"status": "revoked", "revoked_at": NOW, "runs": [], "lease_acquired_at": None}
    return row


@stub.post("/api/v1/runners/{runner_id}/drain", response_model=None)
def drain_runner(runner_id: str) -> Row | JSONResponse:
    WRITES.append(("POST", f"/runners/{runner_id}/drain", {}, None))
    row = _runner_row(runner_id)
    if row is None:
        return JSONResponse({"detail": f"runner {runner_id} does not exist"}, status_code=404)
    if row["revoked_at"] is not None:
        return JSONResponse({"detail": f"runner {runner_id} is revoked"}, status_code=409)
    row["drained_at"] = row["drained_at"] or NOW
    return row


def _trail(
    seq: int,
    ago: int,
    event: str,
    data: Row,
    actor: str = "runner",
    source: str = "runner",
    run_id: str | None = "run_9f21c4",
    runner_id: str | None = ALPHA["id"],
    vm_id: str | None = None,
    lag: int = 0,
) -> Row:
    return {
        "seq": seq,
        "id": f"evt_{seq:032x}",
        "at": NOW - ago,
        "received_at": NOW - ago + lag,
        "source": source,
        "event": event,
        "actor": actor,
        "run_id": run_id,
        "vm_id": vm_id,
        "runner_id": runner_id,
        "data": data,
    }


# The board's trail, oldest first, closed by two rows no writer may produce.
TRAIL: list[Row] = [
    _trail(
        41880,
        540,
        "run_created",
        {"workspace": "alpha", "profile": "build-small"},
        actor="operator",
        source="api",
        runner_id=None,
    ),
    _trail(
        41881,
        538,
        "run_transition",
        {"from": "PENDING", "to": "STARTING", "reason": None},
        source="api",
    ),
    _trail(41882, 530, "vm_created", {}, vm_id="vm_7f3a91", lag=3),
    _trail(
        41887,
        509,
        "network_denied",
        {
            "protocol": "https",
            "host": "registry.example.com",
            "rule": "no rule matched",
            "reason": "denied by policy",
        },
        vm_id="vm_7f3a91",
        lag=4,
    ),
    _trail(
        41890,
        493,
        "shell_allowed",
        {"capability": "read_file", "path": "/workspace/pyproject.toml"},
        lag=4,
    ),
    _trail(
        41895,
        250,
        "diff_reported",
        {"entries": 12, "rejected": 0, "sensitive": 0, "policy": "ask", "decided": False},
        source="api",
        run_id="run_5be317",
        runner_id=GAMMA["id"],
    ),
    _trail(
        41897,
        140,
        "merge_decided",
        {"paths": 12, "resolutions": 3},
        actor="operator",
        source="api",
        run_id="run_5be317",
        runner_id=None,
    ),
    _trail(
        41899,
        50,
        "lease_expired",
        {"lease_id": "lease_7fe201"},
        actor="system",
        source="api",
        run_id=None,
        runner_id="rnr_91ba35dd",
    ),
    _trail(41900, 40, "vm_exploded", {"note": "rm -rf /"}),
    _trail(41901, 30, "run_claimed", {"token": "secret-alpha-value"}),
]
TRAIL_QUERIES: list[Row] = []


@stub.get("/api/v1/audit/summary")
def audit_summary() -> Row:
    return {
        "total": 1284,
        "last_seq": 41902,
        "events_24h": 1284,
        "sources": ["api", "runner"],
        "denials": {"network": 5, "shell": 2, "mcp": 0},
        "refused_24h": 0,
        "spool_lag": 4,
    }


@stub.get("/api/v1/audit/{event_id}", response_model=None)
def audit_event(event_id: str) -> Row | JSONResponse:
    found = next((row for row in TRAIL if row["id"] == event_id), None)
    return found if found else _missing(f"audit event {event_id}")


@stub.get("/api/v1/audit")
def audit(
    runner_id: str | None = None,
    image_id: str | None = None,
    profile_id: str | None = None,
    run_id: str | None = None,
    event: Annotated[list[str] | None, Query()] = None,
    after: int | None = None,
    limit: int = Query(100),
    order: str = "asc",
) -> list[Row]:
    if runner_id is None and image_id is None and profile_id is None:
        TRAIL_QUERIES.append({"run_id": run_id, "event": event, "after": after, "order": order})
        found = [
            row
            for row in TRAIL
            if (run_id is None or row["run_id"] == run_id)
            and (not event or row["event"] in event)
            and (after is None or row["seq"] > after)
        ]
        return (found[::-1] if order == "desc" else found)[:limit]
    if image_id is not None:
        return IMAGE_EVENTS.get(image_id, [])[:limit]
    if profile_id is not None:
        return PROFILE_EVENTS.get(profile_id, [])[:limit]
    rows: list[Row] = [
        {
            "seq": 6,
            "id": "ev_6",
            "at": NOW - 60,
            "source": "api",
            "event": "run_transition",
            "actor": "runner",
            "run_id": "run_9f21c4",
            "vm_id": None,
            "runner_id": runner_id,
            "data": {"from": "STARTED", "to": "FAILED", "reason": "guest exited 1"},
        },
        {
            "seq": 5,
            "id": "ev_5",
            "at": NOW - 120,
            "source": "runner",
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
        "runs_total": 14 if used else 0,
        "runs_24h": 3 if used else 0,
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
                {"protocol": "https", "host": f"{name}.example.com", "ip": None}
                for name in ("alpha", "beta", "gamma")
            ],
            "deny": [{"protocol": None, "host": None, "ip": "192.0.2.10"}],
        },
        "created_at": NOW - 9000,
        "profiles": ["prof_7a1c30", "prof_a93e07", "prof_91ba35"],
        "runs_open": 1,
        "runs_total": 28,
    },
    {
        "id": MNTPOL,
        "kind": "mount",
        "digest": "1" * 64,
        "document": {
            "workdir": "/naos/api",
            "mounts": [
                {"host_path": "/srv/projects/alpha/api", "guest_path": "/naos/api", "mode": "rw"},
                {
                    "host_path": "/srv/alpha-cache",
                    "guest_path": "/home/naos/.cache/alpha",
                    "mode": "ro",
                },
            ],
        },
        "created_at": NOW - 9000,
        "profiles": [],
        "runs_open": 0,
        "runs_total": 0,
    },
    {
        "id": SHELLPOL,
        "kind": "shell",
        "digest": "3" * 64,
        "document": {"allow": ["git_status", "grep", "list_dir", "read_file"]},
        "created_at": NOW - 7000,
        "profiles": [],
        "runs_open": 0,
        "runs_total": 3,
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
                },
                {
                    "name": "beta",
                    "url": "https://beta.example.com/mcp",
                    "tools": ["fetch"],
                    "resources": ["docs://beta/"],
                    "credential": None,
                    "timeout_seconds": 15,
                    "max_calls_per_minute": 120,
                },
            ]
        },
        "created_at": NOW - 9000,
        "profiles": [],
        "runs_open": 1,
        "runs_total": 1,
    },
]
SECRETS: dict[str, Row] = {
    "alpha-token": {
        "id": "sec_alpha",
        "name": "alpha-token",
        "expires_at": NOW + 12 * 86400,
        "created_at": NOW - 9000,
    }
}
IMAGES: list[Row] = [
    {
        "id": "naos-agents",
        "version": "1.4.2",
        "digest": "sha256:3f9a" + "0" * 56 + "c21e",
        "url": "https://images.example.com/a.qcow2",
        "name": "agents",
        "size_bytes": 4_080_218_931,
        "built_at": NOW - 4 * 86400,
        "created_at": NOW - 100,
        "runs_open": 2,
        "runs_total": 5,
    },
    {
        "id": "naos-agents-old",
        "version": "1.3.0",
        "digest": "sha256:" + "b" * 64,
        "url": "https://mirror.example.net/pub/b.qcow2",
        "name": None,
        "size_bytes": None,
        "built_at": None,
        "created_at": NOW - 9000,
        "runs_open": 0,
        "runs_total": 0,
    },
]
IMAGE_EVENTS: dict[str, list[Row]] = {
    "naos-agents": [
        {
            "seq": 9,
            "id": "ev_9",
            "at": NOW - 60,
            "source": "api",
            "event": "run_transition",
            "actor": "runner",
            "run_id": "run_9f21c4",
            "vm_id": None,
            "runner_id": None,
            "data": {"from": "STARTING", "to": "FAILED", "reason": "image digest mismatch"},
        },
        {
            "seq": 8,
            "id": "ev_8",
            "at": NOW - 100,
            "source": "api",
            "event": "image_registered",
            "actor": "operator",
            "run_id": None,
            "vm_id": None,
            "runner_id": None,
            "data": {
                "image_id": "naos-agents",
                "version": "1.4.2",
                "digest": "sha256:3f9a" + "0" * 56 + "c21e",
            },
        },
    ]
}
PROFILE_EVENTS: dict[str, list[Row]] = {
    "prof_7a1c30": [
        {
            "seq": 12,
            "id": "ev_12",
            "at": NOW - 120,
            "source": "api",
            "event": "run_created",
            "actor": "operator",
            "run_id": "run_9f21c4",
            "vm_id": None,
            "runner_id": None,
            "data": {"workspace": "alpha", "profile": "build-small"},
        },
        {
            "seq": 11,
            "id": "ev_11",
            "at": NOW - 9000,
            "source": "api",
            "event": "profile_created",
            "actor": "operator",
            "run_id": None,
            "vm_id": None,
            "runner_id": None,
            "data": {"profile_id": "prof_7a1c30", "name": "build-small"},
        },
    ]
}
# Every write the dialog sends, in order, so a test reads what reached the api.
WRITES: list[tuple[str, str, Row, str | None]] = []


def _found(pid: str) -> Row:
    return next(row for row in PROFILES if row["id"] == pid)


@stub.get("/api/v1/profiles")
def list_profiles(q: str | None = None, policy: str | None = None) -> list[Row]:
    needle = (q or "").lower()
    named: list[str] = next((row["profiles"] for row in POLICIES if row["id"] == policy), [])
    return [
        row
        for row in PROFILES
        if (needle in row["name"].lower() or needle in row["id"])
        and (policy is None or row["id"] in named)
    ]


@stub.get("/api/v1/profiles/{pid}", response_model=None)
def get_profile(pid: str) -> Row | JSONResponse:
    found = next((row for row in PROFILES if row["id"] == pid), None)
    return found if found else JSONResponse({"detail": f"profile {pid} does not exist"}, 404)


@stub.delete("/api/v1/profiles/{pid}", response_model=None)
def delete_profile(pid: str) -> Response:
    WRITES.append(("DELETE", f"/profiles/{pid}", {}, None))
    found = _found(pid)
    if found["active_run"]:
        detail = f"profile {found['name']} is used by #128 STARTED. Delete it once they finish."
        return JSONResponse({"detail": detail}, 409)
    return Response(status_code=204)


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
def list_policies(kind: str | None = None, q: str | None = None) -> list[Row]:
    needle = (q or "").lower()
    return [
        row
        for row in POLICIES
        if (kind is None or row["kind"] == kind)
        and (needle in row["id"].lower() or needle in row["digest"])
    ]


# An equivalent document answers 200 with the policy that holds it; a mount outside /srv is 422.
@stub.post("/api/v1/policies", response_model=None)
def create_policy(body: Row) -> Row | JSONResponse:
    WRITES.append(("POST", "/policies", body, None))
    document = body["document"]
    if body["kind"] == "mount" and not document["workspace"]["host_path"].startswith("/srv/"):
        detail = f"host path {document['workspace']['host_path']!r} is outside the allowed roots"
        return JSONResponse({"detail": detail}, 422)
    if body["kind"] == "shell" and sorted(document["allow"]) == POLICIES[2]["document"]["allow"]:
        return JSONResponse(POLICIES[2], 200)
    created = {
        "id": f"{PREFIXES[body['kind']]}_new001",
        "kind": body["kind"],
        "digest": "9" * 64,
        "document": document,
        "created_at": NOW,
        "profiles": [],
        "runs_open": 0,
        "runs_total": 0,
    }
    return JSONResponse(created, 201)


@stub.get("/api/v1/secrets/{name}", response_model=None)
def get_secret(name: str) -> Row | JSONResponse:
    found = SECRETS.get(name)
    return found if found else _missing(f"secret {name}")


@stub.get("/api/v1/images")
def list_images() -> list[Row]:
    return IMAGES


@stub.post("/api/v1/images", response_model=None)
def register_image(body: Row) -> Row | JSONResponse:
    WRITES.append(("POST", "/images", body, None))
    if any(row["id"] == body["id"] for row in IMAGES):
        detail = f"image {body['id']} is already registered with other values"
        return JSONResponse({"detail": detail}, 409)
    return (
        {"name": None, "size_bytes": None, "built_at": None}
        | body
        | {
            "created_at": NOW,
            "runs_open": 0,
            "runs_total": 0,
        }
    )


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


def _entry(path: str, change: str, kind: str = "file", **fields: Any) -> Row:
    return {"path": path, "change": change, "kind": kind} | fields


def _sha(head: str, tail: str) -> str:
    return head + "0" * 56 + tail


MERGES: dict[str, Row] = {
    "run_5be317": {
        "run_id": "run_5be317",
        "entries": [
            _entry(".github/workflows/ci.yml", "modified", size=1331, mode=0o644, sensitive=True),
            _entry("Makefile", "modified", size=2048, mode=0o644, sensitive=True),
            _entry("docs/notes.md", "created", size=2150, mode=0o644),
            _entry("latest", "created", "symlink", target="build/out"),
            _entry("src/api/__init__.py", "modified", size=212, mode=0o644),
            _entry(
                "src/api/handlers.py",
                "modified",
                size=4300,
                mode=0o644,
                base_mode=0o644,
                sha256=_sha("e3b7", "41c2"),
                base_sha256=_sha("9a0d", "77fe"),
            ),
            _entry("src/api/legacy.py", "deleted", size=1843),
            _entry("src/api/routes.py", "renamed", size=3482, **{"from": "src/api/router.py"}),
            _entry("src/api/util", "created", "dir", mode=0o755),
            _entry("src/api/util/strings.py", "created", size=640, mode=0o644),
            _entry("tests/fixtures/sock", "rejected", "other", reason="special file"),
            _entry("tests/test_handlers.py", "modified", size=5734, mode=0o644),
        ],
        "decision": None,
        "conflicts": None,
        "report": None,
        "updated_at": NOW - 240,
    },
    "run_0d4492": {
        "run_id": "run_0d4492",
        "entries": [
            _entry("added.txt", "created", size=6, mode=0o644),
            _entry("notes.txt", "modified", size=5, mode=0o644),
            _entry("old.txt", "deleted", size=4),
            _entry("renamed.txt", "renamed", size=14, **{"from": "moved.txt"}),
        ],
        "decision": {"paths": ["added.txt", "notes.txt", "old.txt", "renamed.txt"]},
        "conflicts": None,
        "report": {
            "applied": ["added.txt", "notes.txt", "old.txt", "renamed.txt"],
            "skipped": [],
            "exported": [],
            "backed_up": ["notes.txt", "old.txt"],
        },
        "updated_at": NOW - 1869,
    },
}


@stub.get("/api/v1/runs/{run_id}/merge", response_model=None)
def get_merge(run_id: str) -> Row | JSONResponse:
    found = MERGES.get(run_id)
    return found if found else _missing(f"merge of {run_id}")


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
