import sqlite3
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, event, make_url
from sqlmodel import Session, create_engine

from naos_api.settings import Settings


def _enable_foreign_keys(dbapi_connection: sqlite3.Connection, _: object) -> None:
    # SQLite keeps foreign key enforcement off by default, per connection.
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def make_engine(settings: Settings) -> Engine:
    if make_url(settings.database_url).get_backend_name() != "sqlite":
        raise ValueError("only sqlite database URLs are supported")
    engine = create_engine(settings.database_url)
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session
