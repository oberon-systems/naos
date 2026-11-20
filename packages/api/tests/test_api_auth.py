import hashlib
import io
import json
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from naos_api import credentials
from naos_api.app import build_v1_router, create_app
from naos_api.cli import main
from naos_api.settings import Settings

OPERATOR_TOKEN = "operator-alpha-" + "0" * 32
MASTER = {"k1": "a" * 64}

Configure = Callable[..., Settings]
Login = Callable[[], credentials.Issued]


@pytest.fixture
def api(settings: Settings, configure: Configure) -> TestClient:
    configure(
        operator_token_sha256=hashlib.sha256(OPERATOR_TOKEN.encode()).hexdigest(),
        master_keys=MASTER,
        master_kid="k1",
    )
    return TestClient(create_app())


@pytest.fixture
def login(api: TestClient) -> Login:
    def issue() -> credentials.Issued:
        answer = api.post(
            "/api/v1/auth/credential", headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"}
        )
        assert answer.status_code == 200, answer.text
        return credentials.Issued(**answer.json())

    return issue


def signed(
    issued: credentials.Issued, method: str, target: str, body: bytes = b"", at: int | None = None
) -> dict[str, str]:
    now = int(time.time()) if at is None else at
    return credentials.headers(issued, method, target, body, now)


def test_v1_routes_are_denied_before_handler_runs(settings: Settings) -> None:
    calls: list[str] = []
    probe = APIRouter()

    @probe.get("/probe")
    def handler() -> dict[str, str]:
        calls.append("reached")
        return {"status": "reached"}

    app = create_app()
    app.include_router(build_v1_router(probe))
    response = TestClient(app).get("/api/v1/probe")

    assert response.status_code == 401
    assert calls == []


def test_the_operator_token_logs_in_and_nothing_else(api: TestClient, login: Login) -> None:
    bearer = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    wrong = {"Authorization": "Bearer operator-beta-" + "0" * 32}

    assert api.get("/api/v1/runs", headers=bearer).status_code == 401
    assert api.post("/api/v1/auth/credential", headers=wrong).status_code == 401
    issued = login()
    assert issued.expires_at - int(time.time()) <= credentials.CREDENTIAL_SECONDS
    assert api.get("/api/v1/runs", headers=signed(issued, "GET", "/api/v1/runs")).status_code == 200


def test_credentials_are_off_without_a_master_key(settings: Settings, configure: Configure) -> None:
    configure(operator_token_sha256=hashlib.sha256(OPERATOR_TOKEN.encode()).hexdigest())
    answer = TestClient(create_app()).post(
        "/api/v1/auth/credential", headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    )

    assert answer.status_code == 503


def test_a_signed_request_is_never_taken_twice(api: TestClient, login: Login) -> None:
    headers = signed(login(), "GET", "/api/v1/runs")

    assert api.get("/api/v1/runs", headers=headers).status_code == 200
    assert api.get("/api/v1/runs", headers=headers).status_code == 401


def test_a_signature_covers_the_method_path_query_and_body(api: TestClient, login: Login) -> None:
    issued = login()
    body = json.dumps({"name": "alpha-token", "value": "v"}).encode()
    for_query = signed(issued, "GET", "/api/v1/runs?state=failed")
    for_body = signed(issued, "POST", "/api/v1/secrets", body)
    content = {"Content-Type": "application/json"}

    assert api.get("/api/v1/runs?state=active", headers=for_query).status_code == 401
    assert (
        api.get("/api/v1/profiles", headers=signed(issued, "GET", "/api/v1/runs")).status_code
        == 401
    )
    tampered = body.replace(b'"v"', b'"w"')
    assert (
        api.post("/api/v1/secrets", content=tampered, headers=for_body | content).status_code == 401
    )
    assert api.delete(
        "/api/v1/runs", headers=signed(issued, "GET", "/api/v1/runs")
    ).status_code in (
        401,
        405,
    )


def test_a_signature_outside_the_window_is_refused(api: TestClient, login: Login) -> None:
    stale = signed(login(), "GET", "/api/v1/runs", at=int(time.time()) - 301)

    assert api.get("/api/v1/runs", headers=stale).status_code == 401


def test_an_expired_credential_is_refused(api: TestClient) -> None:
    gone = f"k1.0.{int(time.time()) - 1}.{'b' * 32}"
    key = credentials.key_of(MASTER, gone)
    assert key is not None
    issued = credentials.Issued(credential=gone, key=key, expires_at=int(time.time()) - 1)

    assert api.get("/api/v1/runs", headers=signed(issued, "GET", "/api/v1/runs")).status_code == 401


def test_a_logout_ends_every_credential_and_a_signed_request_renews_one(
    api: TestClient, login: Login
) -> None:
    first = login()
    renewed = api.post(
        "/api/v1/auth/credential", headers=signed(first, "POST", "/api/v1/auth/credential")
    )
    assert renewed.status_code == 200
    second = credentials.Issued(**renewed.json())

    logout = api.post("/api/v1/auth/logout", headers=signed(second, "POST", "/api/v1/auth/logout"))

    assert logout.status_code == 204
    for ended in (first, second):
        assert (
            api.get("/api/v1/runs", headers=signed(ended, "GET", "/api/v1/runs")).status_code == 401
        )
    assert (
        api.get("/api/v1/runs", headers=signed(login(), "GET", "/api/v1/runs")).status_code == 200
    )


@pytest.mark.parametrize(
    "field",
    [
        {"X-Signature": "\u00e9".encode() * 32},
        {"X-Timestamp": "9" * 5000},
        {"X-Nonce": "zz"},
        {"X-Credential": "k1.0.1.x"},
    ],
)
def test_a_malformed_header_is_refused_without_a_server_error(
    api: TestClient, login: Login, field: dict[str, str | bytes]
) -> None:
    headers: dict[str, str | bytes] = {**signed(login(), "GET", "/api/v1/runs"), **field}

    assert api.get("/api/v1/runs", headers=headers).status_code == 401


def test_a_socket_upgrade_is_signed_as_a_get(api: TestClient, login: Login) -> None:
    path = "/api/v1/runs/run_alpha/attach"
    for headers in ({"Authorization": f"Bearer {OPERATOR_TOKEN}"}, {}):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            api.websocket_connect(path, headers=headers),
        ):
            pass
        assert refused.value.code == 1008

    with (
        pytest.raises(WebSocketDisconnect) as missing,
        api.websocket_connect(path, headers=signed(login(), "GET", path)),
    ):
        pass
    assert missing.value.code != 1008 or "authorized" not in missing.value.reason


def test_a_fresh_app_is_unauthorized_without_a_handler(settings: Settings) -> None:
    app = FastAPI()
    app.include_router(build_v1_router(APIRouter()))

    assert TestClient(app).get("/api/v1/runs").status_code == 404


def test_naos_api_sign_prints_headers_the_api_takes(
    api: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_file = tmp_path / "operator"
    token_file.write_text(OPERATOR_TOKEN + "\n")

    def through_the_test_client(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        answer = api.post(request.full_url, headers=dict(request.header_items()))
        return io.BytesIO(answer.content)

    monkeypatch.setattr(urllib.request, "urlopen", through_the_test_client)
    status = main(
        [
            "sign",
            "GET",
            "/api/v1/runs",
            "--api",
            "http://testserver",
            "--token-file",
            str(token_file),
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    headers = dict(line.split(": ", 1) for line in lines)

    assert status == 0
    assert set(headers) == {"X-Credential", "X-Timestamp", "X-Nonce", "X-Signature"}
    assert api.get("/api/v1/runs", headers=headers).status_code == 200
