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
            "model": {"policy": None},
            "merge": {"policy": "ask"},
            "timeout": 3600,
            "runner": None,
        }
        | (policies or {}),
        "profile_id": None,
        "lease_id": None,
        "mcp_document": None,
        "policy_history": [],
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
MCPSRV = "mcpsrv_7c1e" + "0" * 28
MODELPOL = "modelpol_7c1e" + "0" * 28
MNTPOL = "mntpol_4c1e" + "0" * 28
SHELLPOL = "shellpol_c42f" + "0" * 26
PREFIXES = {
    "mount": "mntpol",
    "network": "netpol",
    "shell": "shellpol",
    "mcp": "mcppol",
    "model": "modelpol",
}
ALPHA = {"id": "rnr_8c1f42aa", "name": "alpha"}
HELD_MCP: Row = {
    "servers": [
        {
            "name": "alpha",
            "url": "https://alpha.example.com/mcp",
            "credential": "alpha-token",
            "timeout_seconds": 30,
            "max_calls_per_minute": 60,
        }
    ],
    "rules": [
        {"server": "alpha", "tool": "delete", "effect": "deny", "arguments": {}},
        {"server": "alpha", "tool": "search", "effect": "allow", "arguments": {}},
        {
            "server": "secrets",
            "tool": "get",
            "effect": "allow",
            "arguments": {"name": {"equals": "agent-key"}},
            "max_calls": 5,
        },
    ],
}
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
        policies={
            "network": {"policy": NETPOL},
            "mcp": {"policy": MCPPOL},
            "model": {"policy": MODELPOL},
        },
        extra={
            "profile_id": "prof_7a1c30",
            "lease_id": "lease_5d2a91",
            "mcp_document": HELD_MCP,
            "policy_history": [
                {
                    "seq": 1,
                    "kind": "mcp",
                    "policy_id": None,
                    "previous_id": MCPPOL,
                    "document": HELD_MCP,
                    "created_at": NOW - 60,
                }
            ],
        },
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
        kinds = ("mounts", "network", "shell", "mcp", "model")
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
        "denials": {"network": 5, "shell": 2, "mcp": 0, "model": 0},
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
    secret: str | None = None,
    event: Annotated[list[str] | None, Query()] = None,
    after: int | None = None,
    limit: int = Query(100),
    order: str = "asc",
) -> list[Row]:
    if secret is not None:
        return SECRET_EVENTS.get(secret, [])[:limit]
    if run_id is not None and event and all(name.startswith("network_") for name in event):
        found = [row for row in NETWORK_EVENTS if row["run_id"] == run_id and row["event"] in event]
        return (found[::-1] if order == "desc" else found)[:limit]
    if run_id is not None and event and all(name.startswith("shell_") for name in event):
        found = [row for row in SHELL_EVENTS if row["run_id"] == run_id and row["event"] in event]
        return (found[::-1] if order == "desc" else found)[:limit]
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
            "model": {"policy": None},
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
            "rules": [
                {
                    "server": "alpha",
                    "tool": "*",
                    "effect": "deny",
                    "arguments": {"scope": {"equals": "admin"}},
                    "max_calls_per_minute": None,
                    "max_calls": None,
                },
                {
                    "server": "beta",
                    "tool": "fetch",
                    "effect": "allow",
                    "arguments": {},
                    "max_calls_per_minute": 10,
                    "max_calls": 100,
                },
                {"server": "beta", "resource": "docs://beta/", "effect": "allow"},
                {
                    "server": "secrets",
                    "tool": "get",
                    "effect": "allow",
                    "arguments": {"name": {"equals": "agent-key"}},
                    "max_calls_per_minute": None,
                    "max_calls": 5,
                },
                {
                    "server": "shell",
                    "tool": "read_file",
                    "effect": "allow",
                    "arguments": {"path": {"prefix": "/workspace/"}},
                    "max_calls_per_minute": None,
                    "max_calls": None,
                },
            ]
        },
        "created_at": NOW - 9000,
        "profiles": [],
        "runs_open": 1,
        "runs_total": 1,
    },
    {
        "id": MODELPOL,
        "kind": "model",
        "digest": "5" * 64,
        "document": {
            "providers": [
                {
                    "name": "alpha",
                    "api": "openai",
                    "url": "https://models.example.com",
                    "credential": "alpha-key",
                    "models": ["alpha-mini"],
                    "timeout_seconds": 600,
                    "max_requests_per_minute": 60,
                }
            ],
            "max_input_tokens": 100000,
            "max_output_tokens": 10000,
        },
        "created_at": NOW - 9500,
        "profiles": [],
        "runs_open": 1,
        "runs_total": 1,
    },
]
DAY = 86400
HOLDER = {"run_id": "run_9f21c4", "seq": 128, "status": "STARTED", "profile_id": "prof_7a1c30"}
EARLIER = {"run_id": "run_0d4492", "seq": 121, "status": "COMPLETED", "profile_id": None}


def secret(name: str, sid: str, state: str, expires: int | None, **fields: Any) -> Row:
    return {
        "id": sid,
        "name": name,
        "expires_at": expires,
        "created_at": NOW - 12 * DAY,
        "rotated_at": None,
        "state": state,
        "named_by": [],
        "held_by": [],
        "runs": [],
    } | fields


# One secret per state the board draws: in use, expiring, expired and never used.
SECRETS: dict[str, Row] = {
    "alpha-key": secret(
        "alpha-key",
        "sec_8c21d0" + "0" * 26,
        "expiring",
        NOW + 5 * DAY,
        named_by=[{"kind": "model", "id": MODELPOL, "server": "alpha"}],
        held_by=[HOLDER],
        runs=[HOLDER | {"issued": 1, "last_at": NOW - 133}],
    ),
    "alpha-token": secret(
        "alpha-token",
        "sec_alpha",
        "valid",
        NOW + 12 * DAY,
        rotated_at=NOW - 3 * DAY,
        named_by=[{"kind": "reg", "id": MCPSRV, "server": "alpha"}],
        held_by=[HOLDER],
        runs=[
            HOLDER | {"issued": 2, "last_at": NOW - 133},
            EARLIER | {"issued": 1, "last_at": NOW - 2280},
        ],
    ),
    "beta-token": secret(
        "beta-token",
        "sec_2b9c7e" + "0" * 26,
        "expired",
        NOW - 2 * DAY,
        named_by=[{"kind": "reg", "id": MCPSRV, "server": "beta"}],
    ),
    "gamma-key": secret("gamma-key", "sec_0f3a55" + "0" * 26, "valid", None),
}


def _secret_event(seq: int, ago: int, event: str, data: Row, run_id: str | None = None) -> Row:
    return {
        "seq": seq,
        "id": f"evt_{seq:032x}",
        "at": NOW - ago,
        "source": "api",
        "event": event,
        "actor": "runner" if run_id else "operator",
        "run_id": run_id,
        "vm_id": None,
        "runner_id": ALPHA["id"] if run_id else None,
        "data": data,
    }


# Newest first, as the web asks for them.
SECRET_EVENTS: dict[str, list[Row]] = {
    "alpha-token": [
        _secret_event(
            25,
            133,
            "credentials_issued",
            {"names": ["alpha-key", "alpha-token"], "ttl": 900},
            "run_9f21c4",
        ),
        _secret_event(
            24,
            600,
            "secret_delete_refused",
            {"name": "alpha-token", "named_by": 1, "held_by": ["run_9f21c4"]},
        ),
        _secret_event(
            23, 2280, "credentials_issued", {"names": ["alpha-token"], "ttl": 900}, "run_0d4492"
        ),
        _secret_event(22, 3 * DAY, "secret_rotated", {"name": "alpha-token"}),
        _secret_event(
            21,
            5 * DAY,
            "secret_expiry_changed",
            {"name": "alpha-token", "from": None, "to": NOW + 12 * DAY},
        ),
        _secret_event(20, 12 * DAY, "secret_created", {"name": "alpha-token"}),
    ]
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


def _server(name: str, credential: str | None, timeout: int, calls: int, **extra: Any) -> Row:
    return {
        "name": name,
        "kind": "external",
        "id": MCPSRV,
        "url": f"https://{name}.example.com/mcp",
        "credential": credential,
        "timeout_seconds": timeout,
        "max_calls_per_minute": calls,
        "disabled_at": None,
        "created_at": NOW - 9000,
        "updated_at": NOW - 9000,
        "policies": [MCPPOL],
        "runs": [],
        "calls": None,
    } | extra


HELD = {"run_id": "run_9f21c4", "seq": 128, "status": "STARTED", "current": True}
PENDING = {"run_id": "run_3a90f8", "seq": 124, "status": "PENDING", "current": False}


def _servers() -> list[Row]:
    built_in = [
        {
            "name": name,
            "kind": "built-in",
            "url": None,
            "policies": [MCPPOL] if name != "network" else [],
            "runs": [HELD] if name != "network" else [],
            "calls": None,
        }
        for name in ("shell", "network", "secrets")
    ]
    return [
        _server("alpha", "alpha-token", 30, 60, runs=[HELD, PENDING], updated_at=NOW - 600),
        _server("beta", None, 15, 120),
        _server("gamma", None, 30, 60, policies=[], disabled_at=NOW - 7200),
        *built_in,
    ]


SERVER_CALLS = {
    "today": 212,
    "denied_today": 4,
    "last": {"at": NOW - 30, "tool": "search", "resource": "", "decision": "allow"},
    "last_failure": {"id": "ev_mcp_fail", "at": NOW - 90, "category": "credential"},
}


@stub.get("/api/v1/mcp-servers")
def list_mcp_servers() -> list[Row]:
    return _servers()


@stub.get("/api/v1/mcp-servers/{name}", response_model=None)
def get_mcp_server(name: str) -> Row | JSONResponse:
    found = next((row for row in _servers() if row["name"] == name), None)
    return found | {"calls": SERVER_CALLS} if found else _missing(f"mcp server {name}")


@stub.post("/api/v1/mcp-servers", response_model=None)
def register_mcp_server(body: Row) -> JSONResponse:
    WRITES.append(("POST", "/mcp-servers", body, None))
    if any(row["name"] == body["name"] for row in _servers()):
        return _refused(f"mcp server {body['name']} already exists", 409)
    if not body["url"].startswith("https://"):
        return _refused("server url must be https", 422)
    created = _server(body["name"], body["credential"], 30, 60, policies=[])
    return JSONResponse(created | body, 201)


@stub.patch("/api/v1/mcp-servers/{name}", response_model=None)
def patch_mcp_server(name: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("PATCH", f"/mcp-servers/{name}", body, None))
    found = next((row for row in _servers() if row["name"] == name), None)
    return found | body if found else _missing(f"mcp server {name}")


@stub.post("/api/v1/mcp-servers/{name}/{act}", response_model=None)
def switch_mcp_server(name: str, act: str) -> Row | JSONResponse:
    WRITES.append(("POST", f"/mcp-servers/{name}/{act}", {}, None))
    found = next((row for row in _servers() if row["name"] == name), None)
    return found if found else _missing(f"mcp server {name}")


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


def _in_use(row: Row) -> bool:
    return bool(row["named_by"] or row["held_by"])


def _words(row: Row) -> list[str]:
    named = [word.lower() for usage in row["named_by"] for word in (usage["id"], usage["server"])]
    return [row["name"], row["id"], *named]


@stub.get("/api/v1/secrets")
def list_secrets(
    q: str | None = None, state: str | None = None, used: bool | None = None
) -> list[Row]:
    needle = (q or "").lower()
    return [
        {key: value for key, value in row.items() if key != "runs"}
        for row in SECRETS.values()
        if (state is None or row["state"] == state)
        and (used is None or _in_use(row) is used)
        and any(needle in word for word in _words(row))
    ]


# A refused value comes back in the answer, as pydantic echoes it; the web must not show it.
@stub.post("/api/v1/secrets", response_model=None)
def create_secret(body: Row) -> JSONResponse:
    WRITES.append(("POST", "/secrets", body, None))
    if body["name"] in SECRETS:
        return _refused(f"secret {body['name']} already exists", 409)
    if not body["value"].isascii():
        detail = {"type": "string_pattern_mismatch", "loc": ["body", "value"]}
        return JSONResponse({"detail": [detail | {"input": body["value"]}]}, 422)
    created = secret(body["name"], "sec_new001", "valid", body["expires_at"])
    return JSONResponse({key: value for key, value in created.items() if key != "runs"}, 201)


@stub.get("/api/v1/secrets/{name}", response_model=None)
def get_secret(name: str) -> Row | JSONResponse:
    found = SECRETS.get(name)
    return found if found else _missing(f"secret {name}")


@stub.post("/api/v1/secrets/{name}/rotate", response_model=None)
def rotate_secret(name: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("POST", f"/secrets/{name}/rotate", body, None))
    found = SECRETS.get(name)
    return found | {"rotated_at": NOW} if found else _missing(f"secret {name}")


@stub.patch("/api/v1/secrets/{name}", response_model=None)
def set_expiry(name: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("PATCH", f"/secrets/{name}", body, None))
    found = SECRETS.get(name)
    return found | {"expires_at": body["expires_at"]} if found else _missing(f"secret {name}")


@stub.delete("/api/v1/secrets/{name}", response_model=None)
def delete_secret(name: str) -> Response:
    WRITES.append(("DELETE", f"/secrets/{name}", {}, None))
    found = SECRETS.get(name)
    if found is None:
        return _missing(f"secret {name}")
    if _in_use(found):
        return _refused(f"secret {name} is in use: named by {MCPSRV} (server alpha)", 409)
    return Response(status_code=204)


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


def _refused(detail: str, code: int) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=code)


# The api's own checks, in its order: a pending decision, then a path outside the diff.
@stub.post("/api/v1/runs/{run_id}/merge", response_model=None)
def decide_merge(run_id: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("POST", f"/runs/{run_id}/merge", body, None))
    merge = MERGES.get(run_id)
    if merge is None:
        return _missing(f"merge of {run_id}")
    if merge["decision"] is not None or merge["report"] is not None:
        return _refused(f"run {run_id} already has a merge decision pending", 409)
    live = {entry["path"] for entry in merge["entries"] if entry["change"] != "rejected"}
    unknown = sorted((set(body["paths"]) | set(body["resolutions"])) - live)
    if unknown:
        return _refused(f"{unknown[0]} is not a mergeable path of the diff", 422)
    merge["decision"] = {"paths": sorted(body["paths"]), "resolutions": body["resolutions"]}
    merge["conflicts"], merge["updated_at"] = None, NOW
    return merge


@stub.post("/api/v1/runs/{run_id}/merge/reject", response_model=None)
def reject_merge(run_id: str) -> Row | JSONResponse:
    WRITES.append(("POST", f"/runs/{run_id}/merge/reject", {}, None))
    merge = MERGES.get(run_id)
    if merge is None:
        return _missing(f"merge of {run_id}")
    if merge["decision"] is not None or merge["report"] is not None:
        return _refused(f"run {run_id} already has a merge decision pending", 409)
    merge["decision"] = {"paths": [], "resolutions": {}}
    merge["conflicts"], merge["updated_at"] = None, NOW
    return merge


# The merged run keeps who decided and the VM its archive is named after.
MERGE_EVENTS: dict[str, list[Row]] = {
    "run_0d4492": [
        _runner(1, NOW - 2270, "vm_created", {}, vm_id="vm_3f0a"),
        _event(2, NOW - 1980, "merge_decided", {"paths": 4, "resolutions": 0}, actor="operator"),
    ],
}


@stub.get("/api/v1/runs/{run_id}/events")
def run_events(run_id: str) -> list[Row]:
    return RUN_EVENTS if run_id == "run_9f21c4" else MERGE_EVENTS.get(run_id, [])


# One Run has called its models: the output budget is nearly spent and two calls were refused.
MODEL_GATE: Row = {
    "calls": 14,
    "denied": 2,
    "input_tokens": 64000,
    "output_tokens": 9800,
    "refusals": [
        {
            "id": "evt_" + "b" * 32,
            "at": NOW - 30,
            "provider": "alpha",
            "model": "alpha-mini",
            "category": "budget",
        },
        {
            "id": "evt_" + "c" * 32,
            "at": NOW - 90,
            "provider": "unknown",
            "model": "beta-max",
            "category": "denied",
        },
    ],
}
IDLE_GATE: Row = {"calls": 0, "denied": 0, "input_tokens": 0, "output_tokens": 0, "refusals": []}


@stub.get("/api/v1/runs/{run_id}/gates/model")
def model_gate(run_id: str) -> Row:
    return MODEL_GATE if run_id == RUNS[0]["id"] else IDLE_GATE


# The started Run reached one host, was denied two, and one event carries a field nobody typed.
NETWORK_EVENTS: list[Row] = [
    _runner(
        40,
        NOW - 100,
        "network_allowed",
        {"protocol": "https", "host": "alpha.example.com", "rule": "allow[0]"},
    ),
    _runner(
        41,
        NOW - 60,
        "network_denied",
        {
            "protocol": "https",
            "host": "<b>private</b>.example.com",
            "rule": "none",
            "reason": "no matching allow rule",
        },
    ),
    _runner(
        42,
        NOW - 30,
        "network_denied",
        {
            "protocol": "https",
            "host": "internal.example.com",
            "rule": "deny[0]",
            "reason": "explicit deny rule",
            "note": "<script>alert(1)</script>",
        },
    ),
]
NETWORK_GATE: Row = {
    "policy_id": NETPOL,
    "document": {"allow": [{"protocol": "https", "host": "alpha.example.com"}], "deny": []},
    "configured_at": NOW - 134,
    "allowed": 41,
    "denied": 3,
    "hosts_total": 3,
    "hosts": [
        {
            "host": "<b>private</b>.example.com",
            "protocol": "https",
            "rule": "none",
            "allowed": 0,
            "denied": 2,
            "last_at": NOW - 60,
        },
        {
            "host": "internal.example.com",
            "protocol": "https",
            "rule": "deny[0]",
            "allowed": 0,
            "denied": 1,
            "last_at": NOW - 30,
        },
        {
            "host": "alpha.example.com",
            "protocol": "https",
            "rule": "allow[0]",
            "allowed": 41,
            "denied": 0,
            "last_at": NOW - 100,
        },
    ],
}
QUIET_NETWORK: Row = {
    "policy_id": None,
    "document": None,
    "configured_at": None,
    "allowed": 0,
    "denied": 0,
    "hosts_total": 0,
    "hosts": [],
}


MCP_GATE: Row = {
    "policy_id": None,
    "document": HELD_MCP,
    "configured_at": NOW - 56,
    "calls": 212,
    "denied": 4,
    "secret_reads": [{"name": "agent-key", "reads": 2}],
}


@stub.get("/api/v1/runs/{run_id}/gates/mcp")
def mcp_gate(run_id: str) -> Row:
    return MCP_GATE


# A stored policy the api does not hold is refused, as the api refuses it.
@stub.post("/api/v1/runs/{run_id}/policies", response_model=None)
def change_policy(run_id: str, body: Row) -> Row | JSONResponse:
    WRITES.append(("POST", f"/runs/{run_id}/policies", body, None))
    if body.get("policy_id") and all(row["id"] != body["policy_id"] for row in POLICIES):
        return _refused(f"mcp policy {body['policy_id']} does not exist", 422)
    return next(row for row in RUNS if row["id"] == run_id)


@stub.get("/api/v1/runs/{run_id}/gates/network")
def network_gate(run_id: str) -> Row:
    return NETWORK_GATE if run_id == RUNS[0]["id"] else QUIET_NETWORK


# The started Run read inside its mount, was refused a path outside it and a capability it lacks.
SHELL_EVENTS: list[Row] = [
    _runner(50, NOW - 100, "shell_allowed", {"capability": "list_dir", "path": "/naos/alpha"}),
    _runner(
        51,
        NOW - 60,
        "shell_denied",
        {
            "capability": "read_file",
            "path": "/naos/<b>alpha</b>/../etc/hosts",
            "reason": "path is outside every mount",
        },
    ),
    _runner(
        52,
        NOW - 30,
        "shell_denied",
        {
            "capability": "git_diff",
            "path": "/naos/alpha",
            "reason": "capability not granted",
            "note": "<script>alert(1)</script>",
        },
    ),
]
SHELL_GATE: Row = {
    "policy_id": SHELLPOL,
    "document": {"allow": ["grep", "list_dir", "read_file"]},
    "configured_at": NOW - 134,
    "roots": ["/naos/alpha"],
    "allowed": 57,
    "denied": 3,
    "called": ["git_diff", "list_dir", "read_file"],
    "groups_total": 3,
    "groups": [
        {
            "capability": "read_file",
            "path": "/naos/<b>alpha</b>/../etc/hosts",
            "allowed": 0,
            "denied": 2,
            "last_at": NOW - 60,
        },
        {
            "capability": "git_diff",
            "path": "/naos/alpha",
            "allowed": 0,
            "denied": 1,
            "last_at": NOW - 30,
        },
        {
            "capability": "list_dir",
            "path": "/naos/alpha",
            "allowed": 57,
            "denied": 0,
            "last_at": NOW - 100,
        },
    ],
}
QUIET_SHELL: Row = {
    "policy_id": None,
    "document": None,
    "configured_at": None,
    "roots": ["/naos/beta"],
    "allowed": 0,
    "denied": 0,
    "called": [],
    "groups_total": 0,
    "groups": [],
}


@stub.get("/api/v1/runs/{run_id}/gates/shell")
def shell_gate(run_id: str) -> Row:
    return SHELL_GATE if run_id == RUNS[0]["id"] else QUIET_SHELL


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
