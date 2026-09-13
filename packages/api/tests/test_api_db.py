from typing import Annotated

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.db import Database, get_session
from naos_api.settings import Settings


def test_schema_can_be_created_twice(settings: Settings) -> None:
    db = Database(settings.database_url)
    db.create_schema()
    db.create_schema()


def test_stale_schema_is_refused_at_startup(settings: Settings) -> None:
    db = Database(settings.database_url)
    with db.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE leases (id VARCHAR(64) PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="table leases does not match the models"):
        db.create_schema()


def test_session_dependency_uses_app_engine(settings: Settings) -> None:
    app = create_app()

    @app.get("/probe")
    def probe(request: Request, session: Annotated[Session, Depends(get_session)]) -> bool:
        return session.get_bind() is request.app.state.db.engine

    assert TestClient(app).get("/probe").json() is True
