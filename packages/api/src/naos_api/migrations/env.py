from alembic import context
from sqlmodel import SQLModel

import naos_api.models  # noqa: F401  registers the tables on SQLModel.metadata

# naos_api.schema hands over a connection opened on NAOS_DATABASE_URL; there is no alembic.ini.
connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=SQLModel.metadata,
    render_as_batch=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
