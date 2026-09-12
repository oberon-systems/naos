import hashlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from naos_api.app import create_app
from naos_api.auth import require_principal
from naos_api.clock import get_now
from naos_api.db import Database
from naos_api.lifecycle import ImageStatus
from naos_api.models import Image
from naos_api.settings import Settings

MOUNT_ROOTS = ["/srv/projects", "/srv/agent-home"]
IMAGE_DIGEST = "sha256:" + "a" * 64
IMAGE_SOURCE = (
    "https://images.example.com/releases/download/packer_{version}/naos-agents-{version}.qcow2"
)
IMAGE_MAX_BYTES = 1024 * 1024
ENROLLMENT_TOKEN = "enroll-alpha-" + "0" * 32
LEASE_TTL = 60
TOKEN_TTL = 3600
EXTERNAL_DATABASE_URL = os.environ.get("NAOS_TEST_DATABASE_URL")


class _Clock:
    def __init__(self) -> None:
        self.now = 1767225600  # 2026-01-01T00:00:00Z

    def __call__(self) -> int:
        return self.now


@pytest.fixture(autouse=True)
def _skip_sqlite_only(request: pytest.FixtureRequest) -> None:
    if EXTERNAL_DATABASE_URL and request.node.get_closest_marker("sqlite_only"):
        pytest.skip("asserts through SQLite-specific SQL")


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    yield Settings(
        database_url=EXTERNAL_DATABASE_URL or f"sqlite:///{tmp_path / 'naos.db'}",
        allowed_mount_roots=MOUNT_ROOTS,
        runner_enrollment_token_sha256=hashlib.sha256(ENROLLMENT_TOKEN.encode()).hexdigest(),
        lease_ttl_seconds=LEASE_TTL,
        runner_token_ttl_seconds=TOKEN_TTL,
        image_store_path=str(tmp_path / "images"),
        image_source_url=IMAGE_SOURCE,
        image_source_allowed_hosts=["objects.example.com"],
        image_max_bytes=IMAGE_MAX_BYTES,
    )
    if EXTERNAL_DATABASE_URL:
        external = Database(EXTERNAL_DATABASE_URL)
        SQLModel.metadata.drop_all(external.engine)
        external.engine.dispose()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def advance(clock: _Clock) -> Callable[[int], None]:
    def move(seconds: int) -> None:
        clock.now += seconds

    return move


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    db = Database(settings.database_url)
    db.create_schema()
    yield db
    db.engine.dispose()


@pytest.fixture
def session(db: Database) -> Iterator[Session]:
    with Session(db.engine) as session:
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
def spec_body(session: Session) -> dict[str, Any]:
    session.add(
        Image(
            id="image_alpha",
            version="1.0.0",
            digest=IMAGE_DIGEST,
            status=ImageStatus.READY,
            size_bytes=0,
        )
    )
    session.commit()
    return {
        "image": {"id": "image_alpha", "digest": IMAGE_DIGEST},
        "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 10},
        "timeout": 3600,
    }


@pytest.fixture
def mount_body() -> dict[str, Any]:
    return {
        "workspace": {"host_path": "/srv/projects/alpha"},
        "home": [{"host_path": "/srv/agent-home/claude", "guest_path": ".claude"}],
    }
