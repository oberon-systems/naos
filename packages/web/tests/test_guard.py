import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from naos_web.app import create_app
from naos_web.settings import get_settings

FOREIGN = "http://example.com"


def test_a_foreign_host_is_refused(client: TestClient) -> None:
    assert client.get("/secrets", headers={"host": "example.com"}).status_code == 400
    assert client.get("/healthz", headers={"host": "example.com:8000"}).status_code == 400


def test_by_default_only_loopback_names_are_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAOS_WEB_ALLOWED_HOSTS")
    get_settings.cache_clear()
    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")

    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz", headers={"host": "localhost"}).status_code == 200
    assert client.get("/healthz", headers={"host": "testserver"}).status_code == 400


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": FOREIGN},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site", "origin": "http://testserver"},
    ],
)
def test_a_cross_origin_write_is_refused_before_the_api_is_called(
    client: TestClient, headers: dict[str, str]
) -> None:
    answer = client.post("/secrets/missing/delete", headers=headers, follow_redirects=False)

    assert answer.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [{}, {"origin": "http://testserver"}, {"sec-fetch-site": "same-origin"}],
)
def test_a_same_origin_write_reaches_its_route(client: TestClient, headers: dict[str, str]) -> None:
    answer = client.post("/secrets/missing/delete", headers=headers, follow_redirects=False)

    assert answer.status_code not in (400, 403)


def test_a_cross_origin_read_is_answered(client: TestClient) -> None:
    assert client.get("/healthz", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def test_a_socket_with_an_opaque_origin_is_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/runs/x/terminal/ws", headers={"origin": "null"}),
    ):
        pass

    assert refused.value.code == 1008
