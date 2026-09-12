from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint

from naos_api.clock import now_ts
from naos_api.lifecycle import ImageStatus, RunStatus
from naos_api.spec import PolicyKind


class PolicySnapshot(SQLModel, table=True):
    __tablename__ = "policy_snapshot"
    __table_args__ = (UniqueConstraint("kind", "digest"),)

    id: str = Field(primary_key=True)
    kind: PolicyKind
    digest: str
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: int = Field(default_factory=now_ts)


class Runner(SQLModel, table=True):
    __tablename__ = "runner"

    id: str = Field(primary_key=True)
    name: str
    token_hash: str = Field(unique=True)
    token_expires_at: int
    prev_token_hash: str | None = Field(default=None, unique=True)
    prev_token_expires_at: int | None = None
    created_at: int = Field(default_factory=now_ts)
    last_heartbeat_at: int | None = None
    revoked_at: int | None = None


class Lease(SQLModel, table=True):
    __tablename__ = "lease"

    id: str = Field(primary_key=True)
    runner_id: str = Field(foreign_key="runner.id", ondelete="RESTRICT")
    # Carries runner_id while the lease is live and NULL once it expires. A plain UNIQUE
    # over it is the portable form of "one live lease per runner": NULLs never collide.
    live_runner_id: str | None = Field(default=None, unique=True)
    acquired_at: int
    expires_at: int
    expired_at: int | None = None


class Run(SQLModel, table=True):
    __tablename__ = "run"

    id: str = Field(primary_key=True)
    seq: int = Field(unique=True)
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
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)


class Image(SQLModel, table=True):
    __tablename__ = "image"

    id: str = Field(primary_key=True)
    version: str
    digest: str = Field(unique=True)
    status: ImageStatus = Field(default=ImageStatus.IMPORTING, index=True)
    status_reason: str | None = None
    size_bytes: int | None = None
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)


# Triggers keep ORM bulk updates and raw SQL off a Run's boundary. MySQL has no UPDATE OF,
# so there the guard compares values and a no-op write of the same value passes.
IMMUTABILITY_DDL: dict[str, list[str]] = {
    "sqlite": [
        "CREATE TRIGGER IF NOT EXISTS run_spec_immutable"
        " BEFORE UPDATE OF id, seq, spec, mount_policy_id, network_policy_id, shell_policy_id,"
        " mcp_policy_id, idempotency_key, request_digest, created_at ON run"
        " BEGIN SELECT RAISE(ABORT, 'run spec is immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS policy_snapshot_no_update"
        " BEFORE UPDATE ON policy_snapshot"
        " BEGIN SELECT RAISE(ABORT, 'policy snapshot is immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS policy_snapshot_no_delete"
        " BEFORE DELETE ON policy_snapshot"
        " BEGIN SELECT RAISE(ABORT, 'policy snapshot is immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS image_identity_immutable"
        " BEFORE UPDATE OF id, version, digest, created_at ON image"
        " BEGIN SELECT RAISE(ABORT, 'image identity is immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS image_no_delete BEFORE DELETE ON image"
        " BEGIN SELECT RAISE(ABORT, 'image identity is immutable'); END",
    ],
    "postgresql": [
        "CREATE OR REPLACE FUNCTION run_spec_immutable() RETURNS trigger AS $$"
        " BEGIN RAISE EXCEPTION 'run spec is immutable'; END; $$ LANGUAGE plpgsql",
        "CREATE OR REPLACE TRIGGER run_spec_immutable"
        " BEFORE UPDATE OF id, seq, spec, mount_policy_id, network_policy_id, shell_policy_id,"
        " mcp_policy_id, idempotency_key, request_digest, created_at ON run"
        " FOR EACH ROW EXECUTE FUNCTION run_spec_immutable()",
        "CREATE OR REPLACE FUNCTION policy_snapshot_immutable() RETURNS trigger AS $$"
        " BEGIN RAISE EXCEPTION 'policy snapshot is immutable'; END; $$ LANGUAGE plpgsql",
        "CREATE OR REPLACE TRIGGER policy_snapshot_no_update BEFORE UPDATE ON policy_snapshot"
        " FOR EACH ROW EXECUTE FUNCTION policy_snapshot_immutable()",
        "CREATE OR REPLACE TRIGGER policy_snapshot_no_delete BEFORE DELETE ON policy_snapshot"
        " FOR EACH ROW EXECUTE FUNCTION policy_snapshot_immutable()",
        "CREATE OR REPLACE FUNCTION image_identity_immutable() RETURNS trigger AS $$"
        " BEGIN RAISE EXCEPTION 'image identity is immutable'; END; $$ LANGUAGE plpgsql",
        "CREATE OR REPLACE TRIGGER image_identity_immutable"
        " BEFORE UPDATE OF id, version, digest, created_at ON image"
        " FOR EACH ROW EXECUTE FUNCTION image_identity_immutable()",
        "CREATE OR REPLACE TRIGGER image_no_delete BEFORE DELETE ON image"
        " FOR EACH ROW EXECUTE FUNCTION image_identity_immutable()",
    ],
    "mysql": [
        "CREATE TRIGGER IF NOT EXISTS run_spec_immutable BEFORE UPDATE ON run FOR EACH ROW"
        " BEGIN IF NOT (NEW.id <=> OLD.id) OR NOT (NEW.seq <=> OLD.seq)"
        " OR NOT (NEW.spec <=> OLD.spec) OR NOT (NEW.mount_policy_id <=> OLD.mount_policy_id)"
        " OR NOT (NEW.network_policy_id <=> OLD.network_policy_id)"
        " OR NOT (NEW.shell_policy_id <=> OLD.shell_policy_id)"
        " OR NOT (NEW.mcp_policy_id <=> OLD.mcp_policy_id)"
        " OR NOT (NEW.idempotency_key <=> OLD.idempotency_key)"
        " OR NOT (NEW.request_digest <=> OLD.request_digest)"
        " OR NOT (NEW.created_at <=> OLD.created_at)"
        " THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'run spec is immutable'; END IF; END",
        "CREATE TRIGGER IF NOT EXISTS policy_snapshot_no_update"
        " BEFORE UPDATE ON policy_snapshot FOR EACH ROW"
        " BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'policy snapshot is immutable'; END",
        "CREATE TRIGGER IF NOT EXISTS policy_snapshot_no_delete"
        " BEFORE DELETE ON policy_snapshot FOR EACH ROW"
        " BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'policy snapshot is immutable'; END",
        "CREATE TRIGGER IF NOT EXISTS image_identity_immutable BEFORE UPDATE ON image FOR EACH ROW"
        " BEGIN IF NOT (NEW.id <=> OLD.id) OR NOT (NEW.version <=> OLD.version)"
        " OR NOT (NEW.digest <=> OLD.digest) OR NOT (NEW.created_at <=> OLD.created_at)"
        " THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'image identity is immutable';"
        " END IF; END",
        "CREATE TRIGGER IF NOT EXISTS image_no_delete BEFORE DELETE ON image FOR EACH ROW"
        " BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'image identity is immutable'; END",
    ],
}
