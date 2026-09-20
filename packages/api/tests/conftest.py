import hashlib
import json
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
from naos_api.models import Image
from naos_api.settings import Settings, get_settings

MOUNT_ROOTS = ["/srv/projects", "/srv/agent-home"]
IMAGE_DIGEST = "sha256:" + "a" * 64
IMAGE_URL = "https://images.example.com/releases/download/image-1.0.0/naos-agents-1.0.0.qcow2"
ENROLLMENT_TOKEN = "enroll-alpha-" + "0" * 32
LEASE_TTL = 60
TOKEN_TTL = 3600
EXTERNAL_DATABASE_URL = os.environ.get("NAOS_TEST_DATABASE_URL")

Configure = Callable[..., Settings]


class _Clock:
    def __init__(self) -> None:
        self.now = 1767225600  # 2026-01-01T00:00:00Z

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Iterator[Configure]:
    def apply(**values: Any) -> Settings:
        for name, value in values.items():
            key = f"NAOS_{name.upper()}"
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value if isinstance(value, str) else json.dumps(value))
        get_settings.cache_clear()
        return get_settings()

    yield apply
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path, configure: Configure) -> Settings:
    settings = configure(
        database_url=EXTERNAL_DATABASE_URL or f"sqlite:///{tmp_path / 'naos.db'}",
        allowed_mount_roots=MOUNT_ROOTS,
        runner_enrollment_token_sha256=hashlib.sha256(ENROLLMENT_TOKEN.encode()).hexdigest(),
        lease_ttl_seconds=LEASE_TTL,
        runner_token_ttl_seconds=TOKEN_TTL,
    )
    if EXTERNAL_DATABASE_URL:
        external = Database(EXTERNAL_DATABASE_URL)
        SQLModel.metadata.drop_all(external.engine)
        external.engine.dispose()
    return settings


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


def _app(clock: _Clock) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_now] = clock
    return app


@pytest.fixture
def client(settings: Settings, clock: _Clock) -> TestClient:
    app = _app(clock)
    app.dependency_overrides[require_principal] = lambda: None
    return TestClient(app)


@pytest.fixture
def raw_client(settings: Settings, clock: _Clock) -> TestClient:
    return TestClient(_app(clock))


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
    session.add(Image(id="image_alpha", version="1.0.0", digest=IMAGE_DIGEST, url=IMAGE_URL))
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


@pytest.fixture
def network_body() -> dict[str, Any]:
    return {
        "allow": [{"protocol": "https", "host": "example.com"}],
        "deny": [{"host": "private.example.com"}],
    }


@pytest.fixture
def shell_body() -> dict[str, Any]:
    return {"allow": ["read_file", "list_dir", "grep"]}


@pytest.fixture
def mcp_body() -> dict[str, Any]:
    return {
        "servers": [
            {
                "name": "alpha",
                "url": "https://mcp.example.com/mcp",
                "tools": ["search", "fetch"],
                "resources": ["docs://alpha/"],
                "credential": "alpha-token",
            }
        ]
    }
