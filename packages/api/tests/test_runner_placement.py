from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

CreateRun = Callable[[str], str]
Register = Callable[..., dict[str, str]]

PLACEMENT = {
    "host": "alpha-01.example.com",
    "zone": "zone-a \u00b7 rack 3",
    "platform": "linux/amd64 \u00b7 Ubuntu 24.04",
    "version": "0.1.0",
    "labels": ["ci", "amd64"],
}


def _register(client: TestClient, headers: dict[str, str], placement: dict[str, Any] | None) -> Any:
    body: dict[str, Any] = {"name": "alpha"}
    if placement is not None:
        body["placement"] = placement
    return client.post("/api/v1/runners/register", json=body, headers=headers)


def _heartbeat(client: TestClient, runner: dict[str, str], **extra: Any) -> Any:
    return client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1, **extra},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )


def _listed(client: TestClient) -> dict[str, Any]:
    rows: list[dict[str, Any]] = client.get("/api/v1/runners").json()
    (row,) = rows
    return row


def test_registration_keeps_where_the_runner_is(
    client: TestClient, enrollment_headers: dict[str, str]
) -> None:
    assert _register(client, enrollment_headers, PLACEMENT).status_code == 201

    placement = _listed(client)["placement"]

    assert placement == {**PLACEMENT, "address": "testclient"}


def test_a_runner_that_reports_nothing_has_an_empty_placement(
    client: TestClient, register: Register
) -> None:
    register()

    placement = _listed(client)["placement"]

    assert placement["host"] is None
    assert placement["labels"] == []
    assert placement["address"] == "testclient"


def test_a_heartbeat_updates_the_placement_and_the_interval(
    client: TestClient, register: Register
) -> None:
    runner = register()

    moved = {**PLACEMENT, "zone": "zone-b"}
    assert _heartbeat(client, runner, interval_seconds=20, placement=moved).status_code == 200

    row = _listed(client)
    assert row["placement"]["zone"] == "zone-b"
    assert row["heartbeat_seconds"] == 20


def test_a_heartbeat_without_placement_keeps_the_last_one(
    client: TestClient, register: Register
) -> None:
    runner = register()
    _heartbeat(client, runner, placement=PLACEMENT)

    assert _heartbeat(client, runner).status_code == 200

    assert _listed(client)["placement"]["host"] == PLACEMENT["host"]


@pytest.mark.parametrize(
    "bad",
    [
        {"zone": "zone-a\u001b[31m"},
        {"zone": "zone-a\u0085"},
        {"platform": "linux\n"},
        {"host": "alpha 01"},
        {"host": "a" * 254},
        {"labels": ["has space"]},
        {"labels": ["-leading"]},
        {"labels": [f"l{n}" for n in range(17)]},
        {"version": "1.0; rm -rf /"},
    ],
)
def test_placement_that_could_reach_a_terminal_is_refused(
    client: TestClient, register: Register, enrollment_headers: dict[str, str], bad: dict[str, Any]
) -> None:
    runner = register()
    placement = {**PLACEMENT, **bad}

    assert _register(client, enrollment_headers, placement).status_code == 422
    assert _heartbeat(client, runner, placement=placement).status_code == 422


def test_an_interval_outside_its_range_is_refused(client: TestClient, register: Register) -> None:
    runner = register()

    assert _heartbeat(client, runner, interval_seconds=0).status_code == 422
    assert _heartbeat(client, runner, interval_seconds=3601).status_code == 422


def test_runs_can_be_listed_by_the_runner_that_held_them(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    alpha = register("alpha")
    beta = register("beta")
    held = create_run("key-1")
    _heartbeat(client, alpha)
    create_run("key-2")

    listed = client.get("/api/v1/runs", params={"runner": alpha["runner_id"]}).json()
    other = client.get("/api/v1/runs", params={"runner": beta["runner_id"]}).json()

    assert [row["id"] for row in listed] == [held]
    assert other == []


def test_the_audit_reads_newest_first_on_request(client: TestClient, register: Register) -> None:
    runner = register()
    _heartbeat(client, runner)

    oldest = client.get("/api/v1/audit", params={"runner_id": runner["runner_id"]}).json()
    newest = client.get(
        "/api/v1/audit", params={"runner_id": runner["runner_id"], "order": "desc"}
    ).json()

    assert [row["seq"] for row in newest] == [row["seq"] for row in reversed(oldest)]
