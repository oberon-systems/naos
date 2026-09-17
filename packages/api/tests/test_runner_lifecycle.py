from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from naos_api import runners, tasks
from naos_api.app import create_app, sweep_leases
from naos_api.db import Database
from naos_api.lifecycle import TaskStatus
from naos_api.models import Task
from naos_api.settings import Settings

S = TaskStatus
LEASE_TTL = 60
Register = Callable[..., dict[str, str]]
CreateTask = Callable[[str], str]
Advance = Callable[[float], None]


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _heartbeat(client: TestClient, runner: dict[str, str], capacity: int = 0) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": capacity},
        headers=_bearer(runner),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    runner["lease_id"] = body["lease"]["id"]
    return body


def _desired(client: TestClient, runner: dict[str, str]) -> Response:
    response: Response = client.get(
        f"/api/v1/runners/{runner['runner_id']}/tasks", headers=_bearer(runner)
    )
    return response


def _desired_ids(client: TestClient, runner: dict[str, str]) -> list[str]:
    return [run["id"] for run in _desired(client, runner).json()["tasks"]]


def _move(
    client: TestClient,
    runner: dict[str, str],
    run_id: str,
    expected: TaskStatus,
    target: TaskStatus,
    *,
    lease_id: str | None = None,
    reason: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "lease_id": lease_id or runner["lease_id"],
        "expected": expected,
        "target": target,
    }
    if reason is not None:
        body["reason"] = reason
    response: Response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/tasks/{run_id}/transition",
        json=body,
        headers=_bearer(runner),
    )
    return response


def _walk(
    client: TestClient, runner: dict[str, str], run_id: str, path: Sequence[TaskStatus]
) -> None:
    for expected, target in zip(path, path[1:], strict=False):
        response = _move(client, runner, run_id, expected, target)
        assert response.status_code == 200, response.text


def _run(session: Session, run_id: str) -> Task:
    session.expire_all()
    run = session.get(Task, run_id)
    assert run is not None
    return run


def test_heartbeat_extends_the_live_lease(
    client: TestClient, register: Register, advance: Advance, clock: Callable[[], int]
) -> None:
    runner = register()
    first = runner["lease_id"]

    advance(LEASE_TTL - 20)
    assert _heartbeat(client, runner)["lease"] == {
        "id": first,
        "expires_at": clock() + LEASE_TTL,
        "ttl_seconds": LEASE_TTL,
    }
    advance(LEASE_TTL - 20)
    assert _heartbeat(client, runner)["lease"]["id"] == first


def test_expired_lease_fails_active_runs_and_releases_pending(
    client: TestClient,
    register: Register,
    create_task: CreateTask,
    session: Session,
    clock: Callable[[], int],
    advance: Advance,
) -> None:
    started, pending = create_task("key-1"), create_task("key-2")
    runner = register()
    _heartbeat(client, runner, capacity=2)
    _walk(client, runner, started, [S.PENDING, S.STARTING, S.STARTED])

    advance(LEASE_TTL)
    assert runners.expire_leases(session, clock()) == [runner["lease_id"]]
    assert runners.expire_leases(session, clock()) == []

    assert _run(session, started).status is S.FAILED
    assert _run(session, started).status_reason == runners.LEASE_EXPIRED_REASON
    assert _run(session, pending).status is S.PENDING
    assert _run(session, pending).lease_id is None
    assert client.get(f"/api/v1/tasks/{started}").json()["status"] == "FAILED"


def test_lease_expiry_is_detected_on_the_next_runner_call(
    client: TestClient, register: Register, create_task: CreateTask, advance: Advance
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING])

    advance(LEASE_TTL)

    assert _desired(client, runner).status_code == 409
    assert client.get(f"/api/v1/tasks/{run_id}").json()["status"] == "FAILED"


def test_new_lease_after_expiry_fences_the_old_one(
    client: TestClient, register: Register, create_task: CreateTask, advance: Advance
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    old_lease = runner["lease_id"]

    advance(LEASE_TTL + 1)
    _heartbeat(client, runner, capacity=1)

    assert runner["lease_id"] != old_lease
    assert _desired_ids(client, runner) == [run_id]
    stale = _move(client, runner, run_id, S.PENDING, S.STARTING, lease_id=old_lease)
    assert stale.status_code == 409
    assert _move(client, runner, run_id, S.PENDING, S.STARTING).status_code == 200


def test_expiry_leaves_waiting_merge_runs_alone(
    client: TestClient,
    register: Register,
    create_task: CreateTask,
    session: Session,
    clock: Callable[[], int],
    advance: Advance,
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING])
    tasks.transition_task(session, run_id, S.COLLECTING, S.WAITING_MERGE)

    advance(LEASE_TTL)
    runners.expire_leases(session, clock())

    assert _run(session, run_id).status is S.WAITING_MERGE


def test_lifespan_sweep_starts_and_stops(settings: Settings) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200


def test_sweep_leases_expires_past_leases(register: Register, db: Database) -> None:
    runner = register()

    # The fixture clock sits in the past, so the wall clock has already outlived the lease.
    assert sweep_leases(db) == [runner["lease_id"]]
    assert sweep_leases(db) == []


def test_capacity_bounds_assignment(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_ids = [create_task(f"key-{i}") for i in range(3)]
    runner = register()

    _heartbeat(client, runner, capacity=2)
    assert _desired_ids(client, runner) == run_ids[:2]
    _heartbeat(client, runner, capacity=2)
    assert _desired_ids(client, runner) == run_ids[:2]
    _heartbeat(client, runner, capacity=3)
    assert _desired_ids(client, runner) == run_ids


def test_zero_capacity_assigns_nothing(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=0)

    assert _desired_ids(client, runner) == []


@pytest.mark.parametrize("capacity", [-1, 65, "1", 1.5])
def test_heartbeat_rejects_bad_capacity(
    client: TestClient, register: Register, capacity: Any
) -> None:
    runner = register()

    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": capacity},
        headers=_bearer(runner),
    )

    assert response.status_code == 422


def test_run_is_assigned_to_one_runner_only(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_ids = [create_task("key-1"), create_task("key-2")]
    alpha, beta = register("alpha"), register("beta")

    _heartbeat(client, alpha, capacity=5)
    _heartbeat(client, beta, capacity=5)

    assert _desired_ids(client, alpha) == run_ids
    assert _desired_ids(client, beta) == []


def test_stale_candidate_is_not_reassigned(
    client: TestClient,
    register: Register,
    create_task: CreateTask,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = create_task("key-1")
    alpha, beta = register("alpha"), register("beta")
    _heartbeat(client, alpha, capacity=1)
    monkeypatch.setattr(runners, "_candidates", lambda s, limit: [run_id])

    _heartbeat(client, beta, capacity=1)

    assert _desired_ids(client, beta) == []
    assert _run(session, run_id).lease_id == alpha["lease_id"]


def test_terminal_runs_are_not_assigned(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    client.post(f"/api/v1/tasks/{run_id}/stop")
    runner = register()

    _heartbeat(client, runner, capacity=1)

    assert _desired_ids(client, runner) == []


def test_desired_state_resolves_policy_documents(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mount_body: dict[str, Any],
) -> None:
    policy = client.post("/api/v1/policies", json={"kind": "mount", "document": mount_body}).json()
    spec_body["mounts"] = {"policy": policy["id"]}
    client.post("/api/v1/tasks", json=spec_body, headers={"Idempotency-Key": "key-1"})
    runner = register()
    _heartbeat(client, runner, capacity=1)

    desired = _desired(client, runner).json()

    assert desired["lease_id"] == runner["lease_id"]
    [run] = desired["tasks"]
    assert run["status"] == "PENDING"
    assert run["spec"]["mounts"]["policy"] == policy["id"]
    assert run["image_url"].startswith("https://images.example.com/")
    assert run["policies"]["mount"] == policy["document"]
    assert run["policies"]["network"] is None


def test_credentials_reach_only_leased_runs_before_they_stop(
    client: TestClient,
    register: Register,
    spec_body: dict[str, Any],
    mcp_body: dict[str, Any],
    clock: Callable[[], int],
) -> None:
    client.post("/api/v1/secrets", json={"name": "alpha-token", "value": "secret-alpha-value"})
    policy = client.post("/api/v1/policies", json={"kind": "mcp", "document": mcp_body}).json()
    spec_body["mcp"] = {"policy": policy["id"]}
    headers = {"Idempotency-Key": "key-1"}
    run_id = client.post("/api/v1/tasks", json=spec_body, headers=headers).json()["id"]
    runner = register()
    _heartbeat(client, runner, capacity=1)

    def credentials() -> Any:
        return _desired(client, runner).json()["tasks"][0]["credentials"]

    issued = {"alpha-token": {"value": "secret-alpha-value", "expires_at": clock() + 300}}

    assert credentials() == issued
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED])
    assert credentials() == issued
    _walk(client, runner, run_id, [S.STARTED, S.STOPPING])
    assert credentials() == {}


def test_operator_stop_reaches_the_runner(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED])

    client.post(f"/api/v1/tasks/{run_id}/stop")

    assert _desired(client, runner).json()["tasks"][0]["status"] == "STOPPING"
    assert _move(client, runner, run_id, S.STOPPING, S.COLLECTING).status_code == 200


def test_runner_drives_the_allowed_path(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)

    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING])

    assert client.get(f"/api/v1/tasks/{run_id}").json()["status"] == "COLLECTING"


@pytest.mark.parametrize(
    ("expected", "target"),
    [
        (S.PENDING, S.CANCELLED),
        (S.PENDING, S.STARTED),
        (S.PENDING, S.FAILED),
        (S.COLLECTING, S.WAITING_MERGE),
        (S.WAITING_MERGE, S.COMPLETED),
    ],
)
def test_runner_cannot_make_forbidden_transitions(
    client: TestClient,
    register: Register,
    create_task: CreateTask,
    expected: TaskStatus,
    target: TaskStatus,
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)

    response = _move(client, runner, run_id, expected, target, reason="boom")

    assert response.status_code == 409
    assert client.get(f"/api/v1/tasks/{run_id}").json()["status"] == "PENDING"


def test_duplicate_claim_is_a_noop(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)

    first = _move(client, runner, run_id, S.PENDING, S.STARTING)
    again = _move(client, runner, run_id, S.PENDING, S.STARTING)

    assert first.status_code == again.status_code == 200
    assert again.json()["status"] == "STARTING"


def test_stale_expected_status_conflicts(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED])

    response = _move(client, runner, run_id, S.STARTING, S.FAILED, reason="vm lost")

    assert response.status_code == 409
    assert client.get(f"/api/v1/tasks/{run_id}").json()["status"] == "STARTED"


def test_failure_requires_and_records_a_reason(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    run_id = create_task("key-1")
    runner = register()
    _heartbeat(client, runner, capacity=1)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING])

    assert _move(client, runner, run_id, S.STARTING, S.FAILED).status_code == 422
    assert _move(client, runner, run_id, S.STARTING, S.FAILED, reason="x" * 501).status_code == 422
    failed = _move(client, runner, run_id, S.STARTING, S.FAILED, reason="vm lost")
    assert failed.status_code == 200
    assert failed.json()["status_reason"] == "vm lost"


def test_foreign_and_unassigned_runs_are_not_found(
    client: TestClient, register: Register, create_task: CreateTask
) -> None:
    owned, unassigned = create_task("key-1"), create_task("key-2")
    alpha, beta = register("alpha"), register("beta")
    _heartbeat(client, alpha, capacity=1)
    _heartbeat(client, beta, capacity=0)

    assert _move(client, beta, owned, S.PENDING, S.STARTING).status_code == 404
    assert _move(client, beta, unassigned, S.PENDING, S.STARTING).status_code == 404
    assert _move(client, beta, "run_missing", S.PENDING, S.STARTING).status_code == 404
    assert (
        _move(client, beta, owned, S.PENDING, S.STARTING, lease_id=alpha["lease_id"]).status_code
        == 404
    )
    assert client.get(f"/api/v1/tasks/{owned}").json()["status"] == "PENDING"
