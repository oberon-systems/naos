from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.db import Database, get_session
from naos_api.settings import Settings


@pytest.mark.sqlite_only
def test_sqlite_connections_enforce_foreign_keys(settings: Settings) -> None:
    with Database(settings.database_url).engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_database_does_not_filter_by_dialect() -> None:
    try:
        # create_engine is lazy, so this builds the engine without reaching the server.
        db = Database("postgresql+psycopg://db.example.com/naos")
    except ModuleNotFoundError as err:
        assert "psycopg" in str(err)
        return

    assert db.dialect == "postgresql"


def test_schema_can_be_created_twice(settings: Settings) -> None:
    db = Database(settings.database_url)
    db.create_schema()
    db.create_schema()


@pytest.mark.sqlite_only
def test_stale_schema_is_refused_at_startup(settings: Settings) -> None:
    db = Database(settings.database_url)
    with db.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE lease (id VARCHAR PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="table lease does not match the models"):
        db.create_schema()


@pytest.mark.sqlite_only
def test_session_dependency_uses_app_engine(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/probe")
    def probe(session: Annotated[Session, Depends(get_session)]) -> int:
        return int(session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one())

    assert TestClient(app).get("/probe").json() == 1
