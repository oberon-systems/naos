import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from naos_api.app import build_v1_router
from naos_api.auth import require_principal


def test_require_principal_always_denies() -> None:
    with pytest.raises(HTTPException) as exc:
        require_principal()

    assert exc.value.status_code == 401


def test_v1_routes_are_denied_before_handler_runs() -> None:
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
