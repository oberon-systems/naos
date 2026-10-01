from typing import Annotated

from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.db import get_session
from naos_api.settings import Settings


def test_session_dependency_uses_app_engine(settings: Settings) -> None:
    app = create_app()

    @app.get("/probe")
    def probe(request: Request, session: Annotated[Session, Depends(get_session)]) -> bool:
        return session.get_bind() is request.app.state.db.engine

    assert TestClient(app).get("/probe").json() is True
