import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from naos_api import consoles
from naos_api.auth import require_operator_connection
from naos_api.models import AuditEvent, ConsoleChunk
from naos_api.settings import Settings

Register = Callable[..., dict[str, str]]
CreateRun = Callable[[str], str]
OPERATOR_TOKEN = "operator-alpha-" + "0" * 32
# what a tmux pane sends once a second while the guest says nothing
REDRAW = b"\x1b[K\r\n" * 3 + b"\x1b[7m\x1b]0;naos\x07\x1b[m\x0f"


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
    assert log.content == b"2026-01-01T00:00:00Z  login: naos\n"


def test_a_quiet_run_ships_an_empty_body(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _ship(client, runner, run_id, 0, b"login: ")

    empty = _ship(client, runner, run_id, 7, b"")

    assert empty.status_code == 200
    assert empty.json() == {"offset": 7}


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


def test_the_log_stamps_each_line_with_the_time_it_arrived(
    client: TestClient,
    register: Register,
    create_run: CreateRun,
    advance: Callable[[int], None],
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _ship(client, runner, run_id, 0, b"naos login: naos\r\nWelcome")
    advance(61)
    _ship(client, runner, run_id, 25, b" to Alpine!\r\n\x1b[K\r\n$ ")

    log = client.get(f"/api/v1/runs/{run_id}/console").content.decode()

    # a line that runs over two chunks keeps the time of the one it began in
    assert log.splitlines() == [
        "2026-01-01T00:00:00Z  naos login: naos",
        "2026-01-01T00:00:00Z  Welcome to Alpine!",
        "2026-01-01T00:01:01Z  $",
    ]


def test_a_chunk_that_only_redraws_the_screen_is_not_stored(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _ship(client, runner, run_id, 0, b"login: naos\r\n")

    reply = _ship(client, runner, run_id, 13, REDRAW)

    assert reply.json() == {"offset": 13 + len(REDRAW)}
    rows = session.exec(select(ConsoleChunk).where(ConsoleChunk.run_id == run_id)).all()
    assert [bytes(row.data) for row in rows] == [b"login: naos\r\n", b""]
    assert client.get(f"/api/v1/runs/{run_id}/console").content == (
        b"2026-01-01T00:00:00Z  login: naos\n"
    )


def test_attach_walks_past_a_dropped_chunk(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _ship(client, runner, run_id, 0, REDRAW)
    _ship(client, runner, run_id, len(REDRAW), b"ready\r\n")
    client.post(f"/api/v1/runs/{run_id}/stop")
    _as_operator(client)

    with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as websocket:
        assert websocket.receive_bytes() == b"ready\r\n"
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_bytes()


def test_the_log_carries_the_text_and_not_the_screen() -> None:
    capture = (
        b"\x1b[?1h\x1b=\x1b[H\x1b[J\x1b[K\r\n"
        b"\x1b[K\r\n"
        b"naos:/naos/alpha$ printf 'beta\\n' > notes.txt\r\n"
        b"\x1b[7m[agent] 0:tmux*\x1b[m\x0f\r\n"
    )

    assert consoles.clean(capture) == (
        b"naos:/naos/alpha$ printf 'beta\\n' > notes.txt\n[agent] 0:tmux*"
    )


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


def _runner_socket(client: TestClient, runner: dict[str, str], run_id: str) -> WebSocketTestSession:
    return client.websocket_connect(
        f"/api/v1/runners/{runner['runner_id']}/runs/{run_id}/console", headers=_bearer(runner)
    )


def test_the_runner_socket_carries_the_output_and_the_keys(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _as_operator(client)

    with _runner_socket(client, runner, run_id) as shipped:
        shipped.send_bytes((0).to_bytes(8, "big") + b"login: naos\r\n")
        assert json.loads(shipped.receive_text()) == {"offset": 13}

        with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as viewer:
            assert viewer.receive_bytes() == b"login: naos\r\n"
            viewer.send_text(json.dumps({"cols": 120, "rows": 30, "view": "panel"}))
            assert json.loads(viewer.receive_text())["driving"] is True
            assert json.loads(shipped.receive_text()) == {"cols": 120, "rows": 30}

            viewer.send_bytes(b"whoami\r")
            assert shipped.receive_bytes() == b"whoami\r"

    typed = session.exec(select(AuditEvent).where(AuditEvent.event == "console_typing")).all()
    assert [(event.actor, event.run_id, event.data["view"]) for event in typed] == [
        ("operator", run_id, "panel")
    ]


# The terminal is fed ahead of the store, so a test that reads the store waits for
# it; an empty report answers with how far the store holds.
def _stored_to(client: TestClient, runner: dict[str, str], run_id: str, end: int) -> None:
    for _ in range(100):
        if _ship(client, runner, run_id, end, b"").json()["offset"] == end:
            return
        time.sleep(0.02)
    raise AssertionError(f"the store never reached {end}")


def test_a_live_viewer_gets_every_byte_the_store_drops(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _as_operator(client)

    with _runner_socket(client, runner, run_id) as shipped:
        shipped.send_bytes((0).to_bytes(8, "big") + b"$ ")
        assert json.loads(shipped.receive_text()) == {"offset": 2}

        with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as viewer:
            assert viewer.receive_bytes() == b"$ "
            shipped.send_bytes((2).to_bytes(8, "big") + b" ")
            assert json.loads(shipped.receive_text()) == {"offset": 3}
            assert viewer.receive_bytes() == b" "
            shipped.send_bytes((3).to_bytes(8, "big") + REDRAW)
            assert json.loads(shipped.receive_text()) == {"offset": 3 + len(REDRAW)}
            assert viewer.receive_bytes() == REDRAW
            _stored_to(client, runner, run_id, 3 + len(REDRAW))

    rows = session.exec(select(ConsoleChunk).where(ConsoleChunk.run_id == run_id)).all()
    assert [bytes(row.data) for row in rows] == [b"$ ", b"", b""]
    assert re.fullmatch(
        rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ  \$\n",
        client.get(f"/api/v1/runs/{run_id}/console").content,
    )
    # with no terminal open nothing is held for one
    assert not cast(FastAPI, client.app).state.console_hub._viewers


def test_a_viewer_that_does_not_drive_types_nothing(
    client: TestClient, register: Register, create_run: CreateRun, session: Session
) -> None:
    runner, run_id = _leased(client, register, create_run)
    _as_operator(client)

    with _runner_socket(client, runner, run_id) as shipped:
        shipped.send_bytes((0).to_bytes(8, "big") + b"$ ")
        assert json.loads(shipped.receive_text()) == {"offset": 2}

        with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as driver:
            driver.receive_bytes()
            driver.send_text(json.dumps({"cols": 100, "rows": 24, "view": "window"}))
            assert json.loads(driver.receive_text())["driving"] is True
            assert json.loads(shipped.receive_text()) == {"cols": 100, "rows": 24}

            with client.websocket_connect(f"/api/v1/runs/{run_id}/attach") as watcher:
                watcher.receive_bytes()
                watcher.send_text(json.dumps({"cols": 90, "rows": 20, "view": "panel"}))
                assert json.loads(watcher.receive_text())["driving"] is False
                watcher.send_bytes(b"rm -rf /\r")

                driver.send_bytes(b"ls\r")
                assert shipped.receive_bytes() == b"ls\r"

    typed = session.exec(select(AuditEvent).where(AuditEvent.event == "console_typing")).all()
    assert [event.data["view"] for event in typed] == ["window"]


def test_a_foreign_runner_cannot_open_the_console_socket(
    client: TestClient, register: Register, create_run: CreateRun
) -> None:
    _, run_id = _leased(client, register, create_run)
    other = register("beta")

    with pytest.raises(WebSocketDisconnect), _runner_socket(client, other, run_id) as shipped:
        shipped.send_bytes((0).to_bytes(8, "big") + b"forged")
        shipped.receive_text()

    assert client.get(f"/api/v1/runs/{run_id}/console").content == b""


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
