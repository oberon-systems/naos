import hashlib
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from naos_api import runs
from naos_api.app import create_app
from naos_api.lifecycle import RunStatus
from naos_api.models import AuditEvent, Run
from naos_api.settings import Settings

S = RunStatus
OPERATOR_TOKEN = "operator-alpha-" + "0" * 32
Register = Callable[..., dict[str, str]]
CreateRun = Callable[[str], str]
Configure = Callable[..., Settings]


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _heartbeat(client: TestClient, runner: dict[str, str], capacity: int = 1) -> int:
    response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": capacity},
        headers=_bearer(runner),
    )
    status: int = response.status_code
    if status == 200:
        runner["lease_id"] = response.json()["lease"]["id"]
    return status


def _walk(
    client: TestClient, runner: dict[str, str], run_id: str, path: Sequence[RunStatus]
) -> None:
    for expected, target in zip(path, path[1:], strict=False):
        response = client.post(
            f"/api/v1/runners/{runner['runner_id']}/runs/{run_id}/transition",
            json={"lease_id": runner["lease_id"], "expected": expected, "target": target},
            headers=_bearer(runner),
        )
        assert response.status_code == 200, response.text


def _run(session: Session, run_id: str) -> Run:
    session.expire_all()
    run = session.get(Run, run_id)
    assert run is not None
    return run


def _events(session: Session, name: str) -> list[AuditEvent]:
    return list(session.exec(select(AuditEvent).where(AuditEvent.event == name)).all())


def _post(client: TestClient, runner_id: str, action: str) -> Any:
    return client.post(f"/api/v1/runners/{runner_id}/{action}")


def test_revoke_answers_with_the_revoked_runner(client: TestClient, register: Register) -> None:
    runner = register()

    response = _post(client, runner["runner_id"], "revoke")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "revoked"
    assert body["revoked_at"] is not None
    assert body["lease_acquired_at"] is None
    assert runner["token"] not in response.text


def test_a_revoked_runner_token_is_refused(client: TestClient, register: Register) -> None:
    runner = register()

    _post(client, runner["runner_id"], "revoke")

    assert _heartbeat(client, runner) == 401
    desired = client.get(f"/api/v1/runners/{runner['runner_id']}/runs", headers=_bearer(runner))
    assert desired.status_code == 401


def test_revoke_fails_the_running_runs_and_requeues_the_pending(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    started, pending = create_run("key-1"), create_run("key-2")
    runner = register()
    _heartbeat(client, runner, capacity=2)
    _walk(client, runner, started, [S.PENDING, S.STARTING, S.STARTED])

    _post(client, runner["runner_id"], "revoke")

    failed = _run(session, started)
    assert failed.status is S.FAILED
    assert failed.status_reason == "runner revoked"
    queued = _run(session, pending)
    assert queued.status is S.PENDING
    assert queued.lease_id is None


def test_revoke_fails_a_run_waiting_for_its_merge(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    run_id = create_run("key-1")
    runner = register()
    _heartbeat(client, runner)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED, S.STOPPING, S.COLLECTING])
    runs.transition_run(session, run_id, S.COLLECTING, S.WAITING_MERGE)

    _post(client, runner["runner_id"], "revoke")

    run = _run(session, run_id)
    assert run.status is S.FAILED
    assert run.status_reason == "runner revoked"


def test_revoke_twice_changes_nothing_the_second_time(
    client: TestClient, register: Register, session: Session
) -> None:
    runner = register()

    first = _post(client, runner["runner_id"], "revoke").json()
    second = _post(client, runner["runner_id"], "revoke")

    assert second.status_code == 200
    assert second.json()["revoked_at"] == first["revoked_at"]
    assert len(_events(session, "runner_revoked")) == 1


def test_revoke_is_recorded_as_the_operator(
    client: TestClient, register: Register, session: Session
) -> None:
    runner = register()

    _post(client, runner["runner_id"], "revoke")

    (event,) = _events(session, "runner_revoked")
    assert event.actor == "operator"
    assert event.runner_id == runner["runner_id"]


@pytest.mark.parametrize("action", ["revoke", "drain"])
def test_an_unknown_runner_is_not_found(client: TestClient, action: str) -> None:
    assert _post(client, "rnr_" + "0" * 32, action).status_code == 404


@pytest.mark.parametrize("action", ["revoke", "drain"])
def test_the_runner_cannot_act_on_itself(
    settings: Settings, configure: Configure, register: Register, action: str
) -> None:
    configure(operator_token_sha256=hashlib.sha256(OPERATOR_TOKEN.encode()).hexdigest())
    runner = register()
    raw = TestClient(create_app())

    unauthenticated = raw.post(f"/api/v1/runners/{runner['runner_id']}/{action}")
    as_runner = raw.post(f"/api/v1/runners/{runner['runner_id']}/{action}", headers=_bearer(runner))

    assert unauthenticated.status_code == 401
    assert as_runner.status_code == 401


def test_a_drained_runner_takes_no_new_run(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner = register()
    _post(client, runner["runner_id"], "drain")
    run_id = create_run("key-1")

    assert _heartbeat(client, runner, capacity=2) == 200

    assert _run(session, run_id).lease_id is None


def test_a_drained_runner_keeps_its_lease_and_its_runs(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    run_id = create_run("key-1")
    runner = register()
    _heartbeat(client, runner)
    _walk(client, runner, run_id, [S.PENDING, S.STARTING, S.STARTED])

    body = _post(client, runner["runner_id"], "drain").json()

    assert body["status"] == "live"
    assert body["drained_at"] is not None
    assert _heartbeat(client, runner) == 200
    _walk(client, runner, run_id, [S.STARTED, S.STOPPING, S.COLLECTING])
    assert _run(session, run_id).status is S.COLLECTING


def test_a_revoked_runner_cannot_be_drained(client: TestClient, register: Register) -> None:
    runner = register()
    _post(client, runner["runner_id"], "revoke")

    assert _post(client, runner["runner_id"], "drain").status_code == 409


def test_drain_twice_is_recorded_once(
    client: TestClient, register: Register, session: Session
) -> None:
    runner = register()

    first = _post(client, runner["runner_id"], "drain").json()
    second = _post(client, runner["runner_id"], "drain").json()

    assert second["drained_at"] == first["drained_at"]
    assert len(_events(session, "runner_drained")) == 1


def test_a_run_cannot_be_pinned_to_a_drained_runner(
    client: TestClient, register: Register, spec_body: dict[str, Any]
) -> None:
    runner = register()
    _post(client, runner["runner_id"], "drain")

    response = client.post(
        "/api/v1/runs",
        json={**spec_body, "runner": runner["runner_id"]},
        headers={"Idempotency-Key": "pinned"},
    )

    assert response.status_code == 422
