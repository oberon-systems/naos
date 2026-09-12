import sqlite3
from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session, SQLModel, create_engine

from naos_api.models import IMMUTABILITY_DDL


def _connect_sqlite(path: str) -> sqlite3.Connection:
    # SQLite keeps foreign key enforcement off by default, per connection.
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)
        self.dialect = self.engine.dialect.name
        if self.dialect == "sqlite":
            path = self.engine.url.database or ":memory:"
            self.engine = create_engine(url, creator=lambda: _connect_sqlite(path))

    def create_schema(self) -> None:
        statements = IMMUTABILITY_DDL.get(self.dialect)
        if statements is None:
            supported = ", ".join(sorted(IMMUTABILITY_DDL))
            raise ValueError(
                f"{self.dialect} cannot enforce run immutability; supported dialects: {supported}"
            )
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for table in SQLModel.metadata.sorted_tables:
                try:
                    connection.execute(table.select().limit(0))
                except Exception as err:
                    raise RuntimeError(
                        f"table {table.name} does not match the models;"
                        " migrate or recreate the database"
                    ) from err
            for statement in statements:
                connection.exec_driver_sql(statement)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.db.engine) as session:
        yield session
