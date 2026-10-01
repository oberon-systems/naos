from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlmodel import SQLModel

import naos_api.models  # noqa: F401  registers the tables on SQLModel.metadata

MIGRATIONS = Path(__file__).with_name("migrations")
FIRST = "0001"
VERSION_TABLE = "alembic_version"


class SchemaError(RuntimeError):
    pass


def config(connection: Connection | None) -> Config:
    alembic_config = Config(attributes={"connection": connection})
    alembic_config.set_main_option("script_location", str(MIGRATIONS))
    return alembic_config


def head() -> str:
    revision = ScriptDirectory.from_config(config(None)).get_current_head()
    if revision is None:
        raise SchemaError(f"no revisions in {MIGRATIONS}")
    return revision


def current(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _known(revision: str) -> bool:
    script = ScriptDirectory.from_config(config(None))
    return any(r.revision == revision for r in script.walk_revisions())


def _has_tables(connection: Connection) -> bool:
    return bool(set(inspect(connection).get_table_names()) & set(SQLModel.metadata.tables))


def _refuse_unknown(revision: str) -> None:
    if not _known(revision):
        raise SchemaError(
            f"the database is at revision {revision}, unknown to this api whose head is {head()}"
        )


def ensure(engine: Engine, auto_migrate: bool) -> None:
    target = head()
    with engine.begin() as connection:
        revision = current(connection)
        if revision == target:
            return
        if revision is not None:
            _refuse_unknown(revision)
            raise SchemaError(
                f"the database is at revision {revision}, behind head {target};"
                " run naos-api migrate"
            )
        if _has_tables(connection):
            raise SchemaError(
                "the database holds tables but no revision; run naos-api migrate to stamp it"
            )
        if not auto_migrate:
            raise SchemaError(
                f"the database is empty; run naos-api migrate to bring it to {target}"
                " or set NAOS_DATABASE_AUTO_MIGRATE"
            )
        command.upgrade(config(connection), "head")


def _shape(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    return {
        table: (
            sorted((c["name"], c["nullable"]) for c in inspector.get_columns(table)),
            sorted(
                str(i["name"])
                for i in inspector.get_indexes(table)
                if not i.get("duplicates_constraint")
            ),
        )
        for table in inspector.get_table_names()
        if table != VERSION_TABLE
    }


def _first_shape() -> dict[str, Any]:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            command.upgrade(config(connection), FIRST)
            return _shape(connection)
    finally:
        engine.dispose()


# A database built by create_all before Alembic took over carries no revision.
def _stamp_unversioned(connection: Connection) -> None:
    found, expected = _shape(connection), _first_shape()
    differ = sorted(t for t in found.keys() | expected.keys() if found.get(t) != expected.get(t))
    if differ:
        raise SchemaError(
            f"the database holds tables but no revision, and {', '.join(differ)}"
            f" differ from revision {FIRST}; it cannot be stamped"
        )
    command.stamp(config(connection), FIRST)


def migrate(engine: Engine) -> str:
    with engine.begin() as connection:
        revision = current(connection)
        if revision is not None:
            _refuse_unknown(revision)
        elif _has_tables(connection):
            _stamp_unversioned(connection)
        command.upgrade(config(connection), "head")
    return head()


def downgrade(engine: Engine) -> str | None:
    with engine.begin() as connection:
        revision = current(connection)
        if revision is None:
            raise SchemaError("the database has no revision to go back from")
        _refuse_unknown(revision)
        command.downgrade(config(connection), "-1")
        return current(connection)
