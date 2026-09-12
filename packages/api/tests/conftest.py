import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.auth import require_principal
from naos_api.clock import get_now
from naos_api.db import make_engine
from naos_api.models import create_schema
from naos_api.settings import Settings

MOUNT_ROOTS = ["/srv/projects", "/srv/agent-home"]
ENROLLMENT_TOKEN = "enroll-alpha-" + "0" * 32
LEASE_TTL = 60
TOKEN_TTL = 3600


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'naos.db'}",
        allowed_mount_roots=MOUNT_ROOTS,
        runner_enrollment_token_sha256=hashlib.sha256(ENROLLMENT_TOKEN.encode()).hexdigest(),
        lease_ttl_seconds=LEASE_TTL,
        runner_token_ttl_seconds=TOKEN_TTL,
    )


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def advance(clock: _Clock) -> Callable[[float], None]:
    def move(seconds: float) -> None:
        clock.now += timedelta(seconds=seconds)

    return move


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = make_engine(settings)
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _app(settings: Settings, clock: _Clock) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_now] = clock
    return app


@pytest.fixture
def client(settings: Settings, clock: _Clock) -> TestClient:
    app = _app(settings, clock)
    app.dependency_overrides[require_principal] = lambda: None
    return TestClient(app)


@pytest.fixture
def raw_client(settings: Settings, clock: _Clock) -> TestClient:
    return TestClient(_app(settings, clock))


@pytest.fixture
def enrollment_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ENROLLMENT_TOKEN}"}


@pytest.fixture
def register(
    client: TestClient, enrollment_headers: dict[str, str]
) -> Callable[..., dict[str, str]]:
    def _register(name: str = "alpha") -> dict[str, str]:
        response = client.post(
            "/api/v1/runners/register", json={"name": name}, headers=enrollment_headers
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return {
            "runner_id": body["runner_id"],
            "token": body["token"]["value"],
            "lease_id": body["lease"]["id"],
        }

    return _register


@pytest.fixture
def create_run(client: TestClient, spec_body: dict[str, Any]) -> Callable[[str], str]:
    def _create(key: str) -> str:
        response = client.post("/api/v1/runs", json=spec_body, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
        run_id: str = response.json()["id"]
        return run_id

    return _create


@pytest.fixture
def spec_body() -> dict[str, Any]:
    return {
        "image": {"id": "image_alpha", "digest": "sha256:" + "a" * 64},
        "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 10},
        "timeout": 3600,
    }


@pytest.fixture
def mount_body() -> dict[str, Any]:
    return {
        "workspace": {"host_path": "/srv/projects/alpha"},
        "home": [{"host_path": "/srv/agent-home/claude", "guest_path": ".claude"}],
    }
