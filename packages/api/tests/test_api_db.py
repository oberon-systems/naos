from typing import Annotated

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.app import create_app
from naos_api.db import get_session
from naos_api.models import Secret
from naos_api.settings import Settings


def test_session_dependency_uses_app_engine(settings: Settings) -> None:
    app = create_app()

    @app.get("/probe")
    def probe(request: Request, session: Annotated[Session, Depends(get_session)]) -> bool:
        return session.get_bind() is request.app.state.db.engine

    assert TestClient(app).get("/probe").json() is True


def test_a_failed_statement_carries_no_bound_value(session: Session) -> None:
    session.add(Secret(id="sec_" + "a" * 32, name="alpha", value="first", expires_at=None))
    session.commit()
    session.add(Secret(id="sec_" + "b" * 32, name="alpha", value="secret-beta-value"))

    try:
        session.commit()
    except Exception as err:  # noqa: BLE001  the driver's error class differs per database
        failed = str(err)
    else:
        pytest.fail("a second secret named alpha was stored")

    assert "INSERT" in failed
    assert "secret-beta-value" not in failed
