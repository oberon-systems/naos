import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, col, select, update

from naos_api.app import create_app
from naos_api.models import Runner
from naos_api.settings import Settings

TOKEN_TTL = 3600
Register = Callable[..., dict[str, str]]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _heartbeat(client: TestClient, runner_id: str, token: str) -> Response:
    response: Response = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat", json={"capacity": 0}, headers=_bearer(token)
    )
    return response


def test_registration_is_disabled_without_enrollment_hash(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'off.db'}")
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/v1/runners/register", json={"name": "alpha"}, headers=_bearer("anything")
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{}, _bearer("wrong"), {"Authorization": "Basic YWxwaGE6YmV0YQ=="}, _bearer("")],
)
def test_registration_rejects_bad_enrollment(
    raw_client: TestClient, headers: dict[str, str]
) -> None:
    response = raw_client.post("/api/v1/runners/register", json={"name": "alpha"}, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("name", ["", "-alpha", "a" * 65, "alpha beta", "alpha/../beta"])
def test_registration_rejects_bad_names(
    raw_client: TestClient, enrollment_headers: dict[str, str], name: str
) -> None:
    response = raw_client.post(
        "/api/v1/runners/register", json={"name": name}, headers=enrollment_headers
    )

    assert response.status_code == 422


def test_token_is_stored_only_as_hash(register: Register, session: Session) -> None:
    runner = register()

    stored = session.get(Runner, runner["runner_id"])
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(runner["token"].encode()).hexdigest()
    assert runner["token"] not in {stored.token_hash, stored.prev_token_hash}


def test_register_response_carries_no_hashes(
    raw_client: TestClient, enrollment_headers: dict[str, str]
) -> None:
    body = raw_client.post(
        "/api/v1/runners/register", json={"name": "alpha"}, headers=enrollment_headers
    ).text

    assert "hash" not in body
    assert "revoked" not in body


def test_runner_token_opens_only_its_own_runner(client: TestClient, register: Register) -> None:
    alpha, beta = register("alpha"), register("beta")

    assert _heartbeat(client, alpha["runner_id"], alpha["token"]).status_code == 200
    assert _heartbeat(client, beta["runner_id"], alpha["token"]).status_code == 403
    assert (
        client.get(
            f"/api/v1/runners/{beta['runner_id']}/runs", headers=_bearer(alpha["token"])
        ).status_code
        == 403
    )


@pytest.mark.parametrize("headers", [{}, _bearer("rnr-forged"), {"Authorization": "Token x"}])
def test_runner_endpoints_require_a_token(
    client: TestClient, register: Register, headers: dict[str, str]
) -> None:
    runner_id = register()["runner_id"]

    response = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat", json={"capacity": 0}, headers=headers
    )

    assert response.status_code == 401


def test_enrollment_token_is_not_a_runner_token(
    client: TestClient, register: Register, enrollment_headers: dict[str, str]
) -> None:
    runner_id = register()["runner_id"]

    response = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat", json={"capacity": 0}, headers=enrollment_headers
    )

    assert response.status_code == 401


def test_runner_token_is_not_an_operator_principal(
    raw_client: TestClient, register: Register
) -> None:
    token = register()["token"]

    assert raw_client.get("/api/v1/runs", headers=_bearer(token)).status_code == 401
    assert raw_client.post("/api/v1/runs/run_x/stop", headers=_bearer(token)).status_code == 401


def test_expired_token_is_rejected(
    client: TestClient, register: Register, advance: Callable[[float], None]
) -> None:
    runner = register()
    advance(TOKEN_TTL)

    assert _heartbeat(client, runner["runner_id"], runner["token"]).status_code == 401


def test_revoked_token_is_rejected(
    client: TestClient,
    register: Register,
    session: Session,
    clock: Callable[[], datetime],
) -> None:
    runner = register()
    session.exec(
        update(Runner).where(col(Runner.id) == runner["runner_id"]).values(revoked_at=clock())
    )
    session.commit()

    assert _heartbeat(client, runner["runner_id"], runner["token"]).status_code == 401


def test_token_rotates_past_half_life_with_overlap(
    client: TestClient, register: Register, advance: Callable[[float], None]
) -> None:
    runner = register()
    runner_id, old = runner["runner_id"], runner["token"]

    assert _heartbeat(client, runner_id, old).json()["token"] is None
    advance(TOKEN_TTL / 2)
    rotated = _heartbeat(client, runner_id, old).json()["token"]
    assert rotated is not None
    new = rotated["value"]
    assert new != old

    assert _heartbeat(client, runner_id, new).json()["token"] is None
    advance(TOKEN_TTL / 2 - 1)
    assert _heartbeat(client, runner_id, old).status_code == 200


def test_previous_token_expires_on_schedule(
    client: TestClient, register: Register, advance: Callable[[float], None]
) -> None:
    runner = register()
    runner_id, old = runner["runner_id"], runner["token"]
    advance(TOKEN_TTL / 2)
    new = _heartbeat(client, runner_id, old).json()["token"]["value"]
    advance(TOKEN_TTL / 2)

    assert _heartbeat(client, runner_id, old).status_code == 401
    assert _heartbeat(client, runner_id, new).status_code == 200


def test_lost_rotation_is_reissued_to_previous_token(
    client: TestClient, register: Register, session: Session, advance: Callable[[float], None]
) -> None:
    runner = register()
    runner_id, old = runner["runner_id"], runner["token"]
    advance(TOKEN_TTL / 2)
    lost = _heartbeat(client, runner_id, old).json()["token"]["value"]

    reissued = _heartbeat(client, runner_id, old).json()["token"]["value"]

    assert reissued not in {old, lost}
    assert _heartbeat(client, runner_id, lost).status_code == 401
    assert _heartbeat(client, runner_id, reissued).status_code == 200
    stored = session.exec(select(Runner).where(col(Runner.id) == runner_id)).one()
    assert stored.prev_token_hash == hashlib.sha256(old.encode()).hexdigest()
