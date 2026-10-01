import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect
from sqlmodel import SQLModel

from naos_api import schema
from naos_api.app import create_app
from naos_api.cli import main
from naos_api.db import Database
from naos_api.settings import Settings

Configure = Callable[..., Settings]

PROBE_REVISION = """
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("images", sa.Column("probe", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("images", "probe")
"""


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    db = Database(settings.database_url)
    yield db.engine
    db.engine.dispose()


def revision_of(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return schema.current(connection)


def upgrade_to(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.upgrade(schema.config(connection), revision)


def test_every_revision_goes_up_and_back_down(engine: Engine) -> None:
    script = ScriptDirectory.from_config(schema.config(None))
    revisions = [r.revision for r in reversed(list(script.walk_revisions()))]
    for revision in revisions:
        upgrade_to(engine, revision)
        assert revision_of(engine) == revision

    for expected in reversed([None, *revisions[:-1]]):
        assert schema.downgrade(engine) == expected
    assert set(inspect(engine).get_table_names()) <= {schema.VERSION_TABLE}
    assert schema.migrate(engine) == schema.head()


def test_head_matches_the_models(engine: Engine) -> None:
    schema.migrate(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, SQLModel.metadata)
    assert diff == [], f"the migrations do not build the models: {diff}"


def test_an_empty_database_comes_up_at_head(settings: Settings) -> None:
    app = create_app()
    assert revision_of(app.state.db.engine) == schema.head()


def test_an_empty_database_is_refused_without_auto_migrate(
    settings: Settings, configure: Configure
) -> None:
    configure(database_auto_migrate=False)
    with pytest.raises(schema.SchemaError, match="the database is empty"):
        create_app()


def test_a_database_behind_head_is_refused_until_migrate(
    settings: Settings, engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    shutil.copytree(schema.MIGRATIONS, migrations, ignore=shutil.ignore_patterns("__pycache__"))
    (migrations / "versions" / "0002_probe.py").write_text(PROBE_REVISION)
    upgrade_to(engine, "0001")
    monkeypatch.setattr(schema, "MIGRATIONS", migrations)

    with pytest.raises(schema.SchemaError, match="at revision 0001, behind head 0002"):
        create_app()
    assert main(["migrate"]) == 0
    assert revision_of(create_app().state.db.engine) == "0002"


def test_an_unknown_revision_is_refused(settings: Settings, engine: Engine) -> None:
    schema.migrate(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'ffff'")

    with pytest.raises(schema.SchemaError, match="revision ffff, unknown to this api"):
        create_app()
    assert main(["migrate"]) == 1
    assert main(["downgrade"]) == 1


def test_an_unversioned_database_is_refused_then_stamped(
    settings: Settings, engine: Engine
) -> None:
    upgrade_to(engine, schema.FIRST)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")

    with pytest.raises(schema.SchemaError, match="tables but no revision"):
        create_app()
    assert main(["migrate"]) == 0
    assert revision_of(create_app().state.db.engine) == schema.head()


def test_an_unversioned_database_that_differs_is_not_stamped(
    settings: Settings, engine: Engine
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE leases (id VARCHAR(64) PRIMARY KEY)")

    with pytest.raises(schema.SchemaError, match=r"\bleases\b.* differ from revision 0001"):
        schema.migrate(engine)
    assert revision_of(engine) is None


def test_downgrade_needs_a_revision(settings: Settings) -> None:
    assert main(["downgrade"]) == 1
