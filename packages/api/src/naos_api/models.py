from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint

from naos_api.clock import now_ts
from naos_api.lifecycle import TaskStatus
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


class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: str = Field(primary_key=True)
    seq: int = Field(unique=True)
    status: TaskStatus = Field(default=TaskStatus.PENDING, index=True)
    status_reason: str | None = None
    spec: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    mount_policy_id: str | None = None
    network_policy_id: str | None = None
    shell_policy_id: str | None = None
    mcp_policy_id: str | None = None
    lease_id: str | None = Field(default=None, index=True)
    idempotency_key: str = Field(unique=True)
    request_digest: str
    created_at: int = Field(default_factory=now_ts)
    updated_at: int = Field(default_factory=now_ts)


class Image(SQLModel, table=True):
    __tablename__ = "images"

    id: str = Field(primary_key=True)
    version: str
    digest: str = Field(unique=True)
    url: str
    created_at: int = Field(default_factory=now_ts)
