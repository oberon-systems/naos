from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api import runs
from naos_api.lifecycle import RunStatus
from naos_api.models import Merge, Run

S = RunStatus
CreateRun = Callable[[str], str]
Advance = Callable[[int], None]


def _row(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _summary(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/runs/summary")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _move(session: Session, run_id: str, *path: RunStatus) -> None:
    current = S.PENDING
    for target in path:
        reason = "guest exited 1" if target is S.FAILED else None
        runs.transition_run(session, run_id, current, target, reason)
        current = target


def test_a_run_carries_its_number(client: TestClient, create_run: CreateRun) -> None:
    first = create_run("key-1")
    second = create_run("key-2")

    assert _row(client, first)["seq"] == 1
    assert _row(client, second)["seq"] == 2


def test_a_pending_run_has_neither_start_nor_finish(
    client: TestClient, create_run: CreateRun
) -> None:
    row = _row(client, create_run("key-1"))

    assert row["started_at"] is None
    assert row["finished_at"] is None
    assert row["runner"] is None
    assert row["merge"] is None


def test_starting_stamps_the_start_and_leaves_the_finish(
    client: TestClient, create_run: CreateRun, session: Session
) -> None:
    run_id = create_run("key-1")
    _move(session, run_id, S.STARTING)

    row = _row(client, run_id)

    assert row["started_at"] is not None
    assert row["started_at"] >= row["created_at"]
    assert row["finished_at"] is None


@pytest.mark.parametrize(
    "path",
    [
        (S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE, S.COMPLETED),
        (S.FAILED,),
        (S.CANCELLED,),
    ],
    ids=["completed", "failed", "cancelled"],
)
def test_every_terminal_state_stamps_the_finish(
    client: TestClient, create_run: CreateRun, session: Session, path: tuple[RunStatus, ...]
) -> None:
    run_id = create_run("key-1")
    _move(session, run_id, *path)

    assert _row(client, run_id)["finished_at"] is not None


def test_a_fenced_run_is_finished_too(
    client: TestClient,
    register: Callable[..., dict[str, str]],
    create_run: CreateRun,
    session: Session,
    advance: Advance,
) -> None:
    runner = register()
    run_id = create_run("key-1")
    client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )
    _move(session, run_id, S.STARTING)
    advance(61)
    # The desired-state call sweeps lapsed leases before it finds it has none.
    lapsed = client.get(
        f"/api/v1/runners/{runner['runner_id']}/runs",
        headers={"Authorization": f"Bearer {runner['token']}"},
    )
    assert lapsed.status_code == 409, lapsed.text

    row = _row(client, run_id)
    assert row["status"] == "FAILED"
    assert row["status_reason"] == "runner lease expired"
    assert row["finished_at"] is not None


def test_a_run_names_the_runner_behind_its_lease(
    client: TestClient, register: Callable[..., dict[str, str]], create_run: CreateRun
) -> None:
    runner = register("alpha")
    run_id = create_run("key-1")
    client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )

    assert _row(client, run_id)["runner"] == {"id": runner["runner_id"], "name": "alpha"}


def test_a_run_names_its_workspace(
    client: TestClient, spec_body: dict[str, Any], mount_body: dict[str, Any]
) -> None:
    policy = client.post("/api/v1/policies", json={"kind": "mount", "document": mount_body})
    assert policy.status_code in (200, 201), policy.text
    body = spec_body | {"mounts": {"policy": policy.json()["id"]}}
    created = client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": "key-1"})
    assert created.status_code == 201, created.text

    assert _row(client, created.json()["id"])["workspace"] == "alpha"


def test_a_waiting_run_summarises_its_merge(
    client: TestClient, create_run: CreateRun, session: Session
) -> None:
    run_id = create_run("key-1")
    session.add(
        Merge(
            run_id=run_id,
            entries=[
                {"path": "a.py", "change": "modified"},
                {"path": "b.py", "change": "created"},
                {"path": "c.py", "change": "rejected"},
            ],
            conflicts=[{"path": "a.py", "reason": "changed on the host"}],
        )
    )
    session.commit()

    assert _row(client, run_id)["merge"] == {"changed": 2, "conflicts": 1}


@pytest.mark.parametrize(
    ("state", "path"),
    [
        ("active", (S.STARTING,)),
        ("queued", ()),
        ("waiting_merge", (S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE)),
        ("failed", (S.FAILED,)),
    ],
)
def test_each_state_narrows_the_list(
    client: TestClient,
    create_run: CreateRun,
    session: Session,
    state: str,
    path: tuple[RunStatus, ...],
) -> None:
    wanted = create_run("key-1")
    _move(session, wanted, *path)
    other = create_run("key-2")
    _move(session, other, S.CANCELLED)

    listed = client.get("/api/v1/runs", params={"state": state}).json()

    assert [run["id"] for run in listed] == [wanted]
    assert len(client.get("/api/v1/runs").json()) == 2


def test_the_states_never_overlap(
    client: TestClient, create_run: CreateRun, session: Session
) -> None:
    waiting = create_run("key-1")
    _move(session, waiting, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE)

    seen = {
        state: [run["id"] for run in client.get("/api/v1/runs", params={"state": state}).json()]
        for state in ("active", "queued", "waiting_merge", "failed")
    }

    assert seen["waiting_merge"] == [waiting]
    assert seen["active"] == []


def test_an_unknown_state_is_unprocessable(client: TestClient) -> None:
    assert client.get("/api/v1/runs", params={"state": "everything"}).status_code == 422


def test_summary_is_not_read_as_a_run_id(client: TestClient, create_run: CreateRun) -> None:
    create_run("key-1")

    assert _summary(client)["counts"]["PENDING"] == 1


def test_summary_counts_what_the_tiles_show(
    client: TestClient, create_run: CreateRun, session: Session
) -> None:
    queued = create_run("key-1")
    running = create_run("key-2")
    _move(session, running, S.STARTING, S.STARTED)
    waiting = create_run("key-3")
    _move(session, waiting, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING, S.WAITING_MERGE)
    failed = create_run("key-4")
    _move(session, failed, S.FAILED)

    body = _summary(client)

    assert body["counts"] == {
        "PENDING": 1,
        "STARTING": 0,
        "STARTED": 1,
        "STOPPING": 0,
        "COLLECTING": 0,
        "WAITING_MERGE": 1,
        "COMPLETED": 0,
        "FAILED": 1,
        "CANCELLED": 0,
    }
    assert body["open"] == 3
    assert body["oldest_pending_at"] == _row(client, queued)["created_at"]
    assert body["failed_24h"] == 1
    assert body["last_failure_reason"] == "guest exited 1"


def test_an_older_failure_falls_out_of_the_window(
    client: TestClient, create_run: CreateRun, session: Session
) -> None:
    run_id = create_run("key-1")
    _move(session, run_id, S.FAILED)
    stored = session.get(Run, run_id)
    assert stored is not None and stored.finished_at is not None
    stored.finished_at -= 86401
    session.add(stored)
    session.commit()

    body = _summary(client)

    assert body["failed_24h"] == 0
    assert body["last_failure_reason"] is None


def test_summary_needs_the_operator(raw_client: TestClient) -> None:
    assert raw_client.get("/api/v1/runs/summary").status_code == 401
