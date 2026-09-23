import asyncio
import re
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from stub_api import CONSOLE

from naos_web.client import ApiError

STARTED = "run_9f21c4"
PENDING = "run_3a90f8"
HX = {"HX-Request": "true"}


def test_the_terminal_tab_attaches_through_the_web(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/terminal", headers=HX).text

    assert re.search(rf'run__tab run__tab--active"\s+href="/runs/{STARTED}/terminal"', body)
    assert f'data-stream="/runs/{STARTED}/terminal/ws"' in body
    assert f"ws /api/v1/runs/{STARTED}/attach" in body
    assert f'href="/runs/{STARTED}/terminal/log">Download log</a>' in body
    assert f'href="/runs/{STARTED}/terminal?window=1"' in body
    assert "still running" in body


def test_only_the_terminal_tab_gives_the_panel_a_height(client: TestClient) -> None:
    terminal = client.get(f"/runs/{STARTED}/terminal", headers=HX).text
    overview = client.get(f"/runs/{STARTED}", headers=HX).text

    assert "panel--terminal" in terminal
    assert "panel--terminal" not in overview


def test_a_pending_run_has_not_started_its_terminal(client: TestClient) -> None:
    body = client.get(f"/runs/{PENDING}/terminal", headers=HX).text

    assert "not started yet" in body


def test_the_hint_names_real_commands_and_no_token(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/terminal", headers=HX).text

    assert f"naos-runner console {STARTED}" in body
    assert re.search(rf"wscat -c ws://\S+/api/v1/runs/{STARTED}/attach \\", body)
    assert "$NAOS_TOKEN" in body
    assert "operator-token" not in body
    assert "naos attach" not in body


def test_the_detached_window_stands_alone(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/terminal?window=1").text

    assert 'class="topbar' not in body
    assert f'href="/runs/{STARTED}/terminal">Back to the run</a>' in body
    assert ">attached</span>" in body
    assert "<span data-terminal-keys>read-only</span>" in body
    assert f'data-stream="/runs/{STARTED}/terminal/ws"' in body


def test_the_log_downloads_through_the_api(client: TestClient) -> None:
    response = client.get(f"/runs/{STARTED}/terminal/log")

    assert response.status_code == 200
    assert response.content == CONSOLE
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{STARTED}-console.log"'
    )


def test_the_stream_relays_what_the_api_sends(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def attach(run_id: str, outbox: "asyncio.Queue[str]") -> AsyncIterator[bytes]:
        yield f"{run_id}\r\n".encode()
        yield CONSOLE

    monkeypatch.setattr(client.app.state.api, "attach", attach)  # type: ignore[attr-defined]

    with client.websocket_connect(f"/runs/{STARTED}/terminal/ws") as websocket:
        assert websocket.receive_bytes() == f"{STARTED}\r\n".encode()
        assert websocket.receive_bytes() == CONSOLE
        with pytest.raises(WebSocketDisconnect) as ended:
            websocket.receive_bytes()

    assert ended.value.code == 1000


def test_the_stream_carries_what_the_viewer_types_upstream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the stub sends back whatever it is given, so the relay can be read from the page
    async def attach(run_id: str, outbox: "asyncio.Queue[bytes | str]") -> AsyncIterator[bytes]:
        yield CONSOLE
        while True:
            frame = await outbox.get()
            yield frame if isinstance(frame, bytes) else frame.encode()

    monkeypatch.setattr(client.app.state.api, "attach", attach)  # type: ignore[attr-defined]
    grid = '{"cols": 120, "rows": 30, "view": "panel"}'

    with client.websocket_connect(f"/runs/{STARTED}/terminal/ws") as websocket:
        assert websocket.receive_bytes() == CONSOLE
        websocket.send_text(grid)
        assert websocket.receive_bytes() == grid.encode()
        websocket.send_bytes(b"whoami\r")
        assert websocket.receive_bytes() == b"whoami\r"


def test_a_refused_attach_closes_the_stream_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def attach(run_id: str, outbox: "asyncio.Queue[str]") -> AsyncIterator[bytes]:
        raise ApiError(f"the api refused /runs/{run_id}/attach")
        yield b""

    monkeypatch.setattr(client.app.state.api, "attach", attach)  # type: ignore[attr-defined]

    with (
        client.websocket_connect(f"/runs/{STARTED}/terminal/ws") as websocket,
        pytest.raises(WebSocketDisconnect) as refused,
    ):
        websocket.receive_bytes()

    assert refused.value.code == 1011
    assert refused.value.reason == f"the api refused /runs/{STARTED}/attach"


def test_a_socket_from_another_origin_is_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect(
            f"/runs/{STARTED}/terminal/ws", headers={"Origin": "https://evil.example.com"}
        ),
    ):
        pass

    assert refused.value.code == 1008
