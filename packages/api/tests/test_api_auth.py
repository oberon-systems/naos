import hashlib
from collections.abc import Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from naos_api.app import build_v1_router, create_app
from naos_api.settings import Settings

OPERATOR_TOKEN = "operator-alpha-" + "0" * 32

Configure = Callable[..., Settings]


def test_v1_routes_are_denied_before_handler_runs(settings: Settings) -> None:
    calls: list[str] = []
    probe = APIRouter()

    @probe.get("/probe")
    def handler() -> dict[str, str]:
        calls.append("reached")
        return {"status": "reached"}

    app = FastAPI()
    app.include_router(build_v1_router(probe))
    response = TestClient(app).get("/api/v1/probe")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert calls == []


@pytest.mark.parametrize(
    ("configured", "sent", "status"),
    [
        (None, OPERATOR_TOKEN, 401),
        (OPERATOR_TOKEN, None, 401),
        (OPERATOR_TOKEN, "operator-beta-" + "0" * 32, 401),
        (OPERATOR_TOKEN, OPERATOR_TOKEN, 200),
    ],
)
def test_operator_token_guards_v1_routes(
    settings: Settings, configure: Configure, configured: str | None, sent: str | None, status: int
) -> None:
    configure(operator_token_sha256=configured and hashlib.sha256(configured.encode()).hexdigest())
    headers = {"Authorization": f"Bearer {sent}"} if sent else {}

    assert TestClient(create_app()).get("/api/v1/tasks", headers=headers).status_code == status
