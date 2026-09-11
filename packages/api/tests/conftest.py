from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.auth import require_principal
from naos_api.db import make_engine
from naos_api.models import create_schema
from naos_api.settings import Settings

MOUNT_ROOTS = ["/srv/projects", "/srv/agent-home"]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'naos.db'}", allowed_mount_roots=MOUNT_ROOTS
    )


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


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[require_principal] = lambda: None
    return TestClient(app)


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
