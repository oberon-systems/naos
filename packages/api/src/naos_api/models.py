from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Connection, Engine, Table, UniqueConstraint, event
from sqlmodel import Field, SQLModel

from naos_api.lifecycle import RunStatus
from naos_api.spec import PolicyKind


def utcnow() -> datetime:
    return datetime.now(UTC)


class PolicySnapshot(SQLModel, table=True):
    __tablename__ = "policy_snapshot"
    __table_args__ = (UniqueConstraint("kind", "digest"),)

    id: str = Field(primary_key=True)
    kind: PolicyKind
    digest: str
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    __tablename__ = "run"

    id: str = Field(primary_key=True)
    status: RunStatus = Field(default=RunStatus.PENDING, index=True)
    status_reason: str | None = None
    spec: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    mount_policy_id: str | None = Field(
        default=None, foreign_key="policy_snapshot.id", ondelete="RESTRICT"
    )
    network_policy_id: str | None = Field(
        default=None, foreign_key="policy_snapshot.id", ondelete="RESTRICT"
    )
    shell_policy_id: str | None = Field(
        default=None, foreign_key="policy_snapshot.id", ondelete="RESTRICT"
    )
    mcp_policy_id: str | None = Field(
        default=None, foreign_key="policy_snapshot.id", ondelete="RESTRICT"
    )
    idempotency_key: str = Field(unique=True)
    request_digest: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# Enforced in the database so neither ORM bulk updates nor raw SQL can change a Run's boundary.
_IMMUTABILITY_TRIGGERS = {
    "run": [
        "CREATE TRIGGER run_spec_immutable BEFORE UPDATE OF id, spec, mount_policy_id,"
        " network_policy_id, shell_policy_id, mcp_policy_id, idempotency_key,"
        " request_digest, created_at ON run"
        " BEGIN SELECT RAISE(ABORT, 'run spec is immutable'); END",
    ],
    "policy_snapshot": [
        "CREATE TRIGGER policy_snapshot_no_update BEFORE UPDATE ON policy_snapshot"
        " BEGIN SELECT RAISE(ABORT, 'policy snapshot is immutable'); END",
        "CREATE TRIGGER policy_snapshot_no_delete BEFORE DELETE ON policy_snapshot"
        " BEGIN SELECT RAISE(ABORT, 'policy snapshot is immutable'); END",
    ],
}


def _create_triggers(target: Table, connection: Connection, **_: Any) -> None:
    for statement in _IMMUTABILITY_TRIGGERS[target.name]:
        connection.exec_driver_sql(statement)


for _table in _IMMUTABILITY_TRIGGERS:
    event.listen(SQLModel.metadata.tables[_table], "after_create", _create_triggers)


def create_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
