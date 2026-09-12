from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    DateTime,
    Dialect,
    Engine,
    Index,
    Table,
    TypeDecorator,
    UniqueConstraint,
    event,
    text,
)
from sqlmodel import Field, SQLModel

from naos_api.lifecycle import RunStatus
from naos_api.spec import PolicyKind


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    # SQLite drops tzinfo, so values are stored as naive UTC and read back as aware UTC.
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not accepted")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class PolicySnapshot(SQLModel, table=True):
    __tablename__ = "policy_snapshot"
    __table_args__ = (UniqueConstraint("kind", "digest"),)

    id: str = Field(primary_key=True)
    kind: PolicyKind
    digest: str
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class Runner(SQLModel, table=True):
    __tablename__ = "runner"

    id: str = Field(primary_key=True)
    name: str
    token_hash: str = Field(unique=True)
    token_expires_at: datetime = Field(sa_type=UtcDateTime)
    prev_token_hash: str | None = Field(default=None, unique=True)
    prev_token_expires_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)
    last_heartbeat_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    revoked_at: datetime | None = Field(default=None, sa_type=UtcDateTime)


class Lease(SQLModel, table=True):
    __tablename__ = "lease"
    __table_args__ = (
        Index(
            "lease_one_live_per_runner",
            "runner_id",
            unique=True,
            sqlite_where=text("expired_at IS NULL"),
        ),
    )

    id: str = Field(primary_key=True)
    runner_id: str = Field(foreign_key="runner.id", ondelete="RESTRICT")
    acquired_at: datetime = Field(sa_type=UtcDateTime)
    expires_at: datetime = Field(sa_type=UtcDateTime)
    expired_at: datetime | None = Field(default=None, sa_type=UtcDateTime)


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
    lease_id: str | None = Field(
        default=None, foreign_key="lease.id", ondelete="RESTRICT", index=True
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
