from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.models import Runner

LEASE_TTL = 60
Register = Callable[..., dict[str, str]]
Advance = Callable[[int], None]
CreateRun = Callable[[str], str]


def _list(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/v1/runners")
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()
    return rows


def _heartbeat(client: TestClient, runner: dict[str, str], capacity: int) -> None:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": capacity},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )
    assert response.status_code == 200, response.text


def test_the_list_is_empty_without_runners(client: TestClient) -> None:
    assert _list(client) == []


def test_a_registered_runner_is_live_and_carries_no_secret(
    client: TestClient, register: Register
) -> None:
    runner = register("alpha")

    (row,) = _list(client)

    assert set(row) == {
        "id",
        "name",
        "status",
        "runs",
        "created_at",
        "last_heartbeat_at",
        "lease_expires_at",
    }
    assert row["id"] == runner["runner_id"]
    assert row["name"] == "alpha"
    assert row["status"] == "live"
    assert row["runs"] == 0
    assert row["last_heartbeat_at"] is None
    assert row["lease_expires_at"] is not None


def test_a_runner_whose_lease_ran_out_is_stale(
    client: TestClient, register: Register, advance: Advance
) -> None:
    register()
    advance(LEASE_TTL + 1)

    (row,) = _list(client)

    assert row["status"] == "stale"
    assert row["lease_expires_at"] is None
    assert row["runs"] == 0


def test_a_revoked_runner_says_so(client: TestClient, register: Register, session: Session) -> None:
    runner = register()
    stored = session.get(Runner, runner["runner_id"])
    assert stored is not None
    stored.revoked_at = stored.created_at + 1
    session.add(stored)
    session.commit()

    (row,) = _list(client)

    assert row["status"] == "revoked"


def test_the_runs_a_runner_holds_are_counted(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    runner = register()
    create_run("counted")
    _heartbeat(client, runner, capacity=1)

    (row,) = _list(client)

    assert row["runs"] == 1
    assert row["last_heartbeat_at"] is not None


def test_the_newest_runner_comes_first(
    client: TestClient, register: Register, advance: Advance
) -> None:
    first = register("alpha")
    advance(1)
    second = register("beta")

    assert [row["id"] for row in _list(client)] == [second["runner_id"], first["runner_id"]]
