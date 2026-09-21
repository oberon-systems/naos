import hashlib
from collections.abc import Callable
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from naos_api.auth import require_operator_connection
from naos_api.models import AuditEvent
from naos_api.settings import Settings

Register = Callable[..., dict[str, str]]
CreateRun = Callable[[str], str]
OPERATOR_TOKEN = "operator-alpha-" + "0" * 32


def _bearer(runner: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {runner['token']}"}


def _leased(
    client: TestClient, register: Register, create_run: CreateRun
) -> tuple[dict[str, str], str]:
    runner = register()
    run_id = create_run("console-alpha")
    client.post(
        f"/api/v1/runners/{runner['runner_id']}/heartbeat",
        json={"capacity": 1},
        headers=_bearer(runner),
    )
    desired = client.get(f"/api/v1/runners/{runner['runner_id']}/runs", headers=_bearer(runner))
    assert [run["id"] for run in desired.json()["runs"]] == [run_id]
    return runner, run_id


def _as_operator(client: TestClient) -> None:
    cast(FastAPI, client.app).dependency_overrides[require_operator_connection] = lambda: None


def _ship(
    client: TestClient, runner: dict[str, str], run_id: str, offset: int, data: bytes
) -> Response:
    response: Response = client.post(
        f"/api/v1/runners/{runner['runner_id']}/runs/{run_id}/console",
        params={"offset": offset},
        content=data,
        headers=_bearer(runner) | {"Content-Type": "application/octet-stream"},
    )
    return response


def test_console_output_is_appended_in_order(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    runner, run_id = _leased(client, register, create_run)

    assert _ship(client, runner, run_id, 0, b"login: ").json() == {"offset": 7}
    assert _ship(client, runner, run_id, 0, b"login: naos\r\n").json() == {"offset": 13}
    assert _ship(client, runner, run_id, 20, b"lost").json() == {"offset": 13}

    log = client.get(f"/api/v1/runs/{run_id}/console")
    assert log.status_code == 200
    assert log.content == b"login: naos\r\n"


def test_a_full_console_log_refuses_more(
    client: TestClient,
    register: Register,
    create_run: CreateRun,
    configure: Callable[..., Settings],
) -> None:
    configure(console_limit_bytes=4096)
    runner, run_id = _leased(client, register, create_run)

    assert _ship(client, runner, run_id, 0, b"x" * 5000).json() == {"offset": 4096}
    assert _ship(client, runner, run_id, 4096, b"y").status_code == 413


def test_a_foreign_runner_cannot_write_the_console(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    _, run_id = _leased(client, register, create_run)
    other = register("beta")

    assert _ship(client, other, run_id, 0, b"forged").status_code == 409
    assert client.get(f"/api/v1/runs/{run_id}/console").content == b""


def test_attach_streams_the_log_and_leaves_an_audit_entry(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _ship(client, runner, run_id, 0, b"login: naos\r\n")
    client.post(f"/api/v1/runs/{run_id}/stop")
    _as_operator(client)

    with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as websocket:
        assert websocket.receive_bytes() == b"login: naos\r\n"
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_bytes()

    attached = session.exec(select(AuditEvent).where(AuditEvent.event == "console_attached")).all()
    assert [(event.actor, event.run_id, event.source) for event in attached] == [
        ("operator", run_id, "api")
    ]


@pytest.mark.parametrize("token", [None, "operator-beta-" + "0" * 32])
def test_attach_needs_the_operator_token(
    raw_client: TestClient, configure: Callable[..., Settings], token: str | None
) -> None:
    configure(operator_token_sha256=hashlib.sha256(OPERATOR_TOKEN.encode()).hexdigest())
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        raw_client.websocket_connect("/api/v1/runs/run_alpha/attach", headers=headers),
    ):
        pass

    assert refused.value.code == 1008


def test_attach_to_an_unknown_run_is_refused(client: TestClient) -> None:
    _as_operator(client)

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/api/v1/runs/run_missing/attach"),
    ):
        pass

    assert refused.value.code == 1008
