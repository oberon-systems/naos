from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.db import get_session, make_engine
from naos_api.settings import Settings


def test_engine_enforces_foreign_keys(settings: Settings) -> None:
    with make_engine(settings).connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_engine_rejects_non_sqlite_url() -> None:
    with pytest.raises(ValueError, match="sqlite"):
        make_engine(Settings(database_url="postgresql://db.example.com/naos"))


def test_session_dependency_uses_app_engine(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/probe")
    def probe(session: Annotated[Session, Depends(get_session)]) -> int:
        return int(session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one())

    assert TestClient(app).get("/probe").json() == 1
