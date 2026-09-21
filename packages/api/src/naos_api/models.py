from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint

from naos_api.clock import now_ts
from naos_api.lifecycle import RunStatus
from naos_api.spec import PolicyKind


class Policy(SQLModel, table=True):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("kind", "digest"),)

    id: str = Field(primary_key=True)
    kind: PolicyKind
    digest: str
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: int = Field(default_factory=now_ts)


class Runner(SQLModel, table=True):
    __tablename__ = "agents"

    id: str = Field(primary_key=True)
    name: str
    token_hash: str = Field(unique=True)
    token_expires_at: int
    prev_token_hash: str | None = Field(default=None, unique=True)
    prev_token_expires_at: int | None = None
    created_at: int = Field(default_factory=now_ts)
    last_heartbeat_at: int | None = None
    # The capacity of the last heartbeat, kept so the slots of a runner whose
    # lease has lapsed are still known.
    capacity: int | None = None
    revoked_at: int | None = None


class Lease(SQLModel, table=True):
    __tablename__ = "leases"

    id: str = Field(primary_key=True)
    runner_id: str = Field(index=True)
    # Carries runner_id while the lease is live and NULL once it expires. A plain UNIQUE
    # over it is the portable form of "one live lease per runner": NULLs never collide.
    live_runner_id: str | None = Field(default=None, unique=True)
    acquired_at: int
    expires_at: int
    expired_at: int | None = None


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    seq: int = Field(unique=True)
    status: RunStatus = Field(default=RunStatus.PENDING, index=True)
    status_reason: str | None = None
    spec: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    mount_policy_id: str | None = None
    network_policy_id: str | None = None
    shell_policy_id: str | None = None
    mcp_policy_id: str | None = None
    profile_id: str | None = Field(default=None, index=True)
    runner_id: str | None = Field(default=None, index=True)
    lease_id: str | None = Field(default=None, index=True)
    idempotency_key: str = Field(unique=True)
    request_digest: str
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)
    started_at: int | None = None
    finished_at: int | None = None


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: str = Field(primary_key=True)
    name: str = Field(unique=True)
    spec: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    digest: str
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)


class Merge(SQLModel, table=True):
    __tablename__ = "merges"

    run_id: str = Field(primary_key=True)
    entries: list[dict[str, Any]] = Field(sa_column=Column(JSON, nullable=False))
    decision: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    conflicts: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    report: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)


class Secret(SQLModel, table=True):
    __tablename__ = "secrets"

    id: str = Field(primary_key=True)
    name: str = Field(unique=True)
    value: str
    expires_at: int | None = None
    created_at: int = Field(default_factory=now_ts)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    seq: int | None = Field(default=None, primary_key=True)
    id: str = Field(unique=True)
    at: int
    source: str
    event: str = Field(index=True)
    actor: str
    run_id: str | None = Field(default=None, index=True)
    vm_id: str | None = None
    runner_id: str | None = Field(default=None, index=True)
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))


class Image(SQLModel, table=True):
    __tablename__ = "images"

    id: str = Field(primary_key=True)
    version: str
    digest: str = Field(unique=True)
    url: str
    created_at: int = Field(default_factory=now_ts)
