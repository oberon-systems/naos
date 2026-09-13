from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session, SQLModel, create_engine

import naos_api.models  # noqa: F401  registers the tables on SQLModel.metadata


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)

    def create_schema(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            for table in SQLModel.metadata.sorted_tables:
                try:
                    connection.execute(table.select().limit(0))
                except Exception as err:
                    raise RuntimeError(
                        f"table {table.name} does not match the models;"
                        " migrate or recreate the database"
                    ) from err


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.db.engine) as session:
        yield session
