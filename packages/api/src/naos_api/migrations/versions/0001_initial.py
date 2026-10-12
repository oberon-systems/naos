"""The schema the models defined when Alembic took it over.

Revision ID: 0001
Revises:
"""

from alembic import op
from sqlmodel import (
    JSON,
    Column,
    Enum,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

RUN_STATUS = Enum(
    "PENDING",
    "STARTING",
    "STARTED",
    "STOPPING",
    "COLLECTING",
    "WAITING_MERGE",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="runstatus",
)
POLICY_KIND = Enum("MOUNT", "NETWORK", "SHELL", "MCP", "MODEL", name="policykind")


def upgrade() -> None:
    op.create_table(
        "agents",
        Column("id", String(), nullable=False),
        Column("name", String(), nullable=False),
        Column("token_hash", String(), nullable=False),
        Column("token_expires_at", Integer(), nullable=False),
        Column("prev_token_hash", String(), nullable=True),
        Column("prev_token_expires_at", Integer(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        Column("last_heartbeat_at", Integer(), nullable=True),
        Column("capacity", Integer(), nullable=True),
        Column("revoked_at", Integer(), nullable=True),
        Column("drained_at", Integer(), nullable=True),
        Column("host", String(), nullable=True),
        Column("address", String(), nullable=True),
        Column("zone", String(), nullable=True),
        Column("platform", String(), nullable=True),
        Column("version", String(), nullable=True),
        Column("labels", JSON(), nullable=False),
        Column("heartbeat_seconds", Integer(), nullable=True),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("token_hash"),
        UniqueConstraint("prev_token_hash"),
    )
    op.create_table(
        "audit_events",
        Column("seq", Integer(), nullable=False),
        Column("id", String(), nullable=False),
        Column("at", Integer(), nullable=False),
        Column("received_at", Integer(), nullable=False),
        Column("source", String(), nullable=False),
        Column("event", String(), nullable=False),
        Column("actor", String(), nullable=False),
        Column("run_id", String(), nullable=True),
        Column("vm_id", String(), nullable=True),
        Column("runner_id", String(), nullable=True),
        Column("data", JSON(), nullable=False),
        PrimaryKeyConstraint("seq"),
        UniqueConstraint("id"),
    )
    op.create_index("ix_audit_events_event", "audit_events", ["event"])
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])
    op.create_index("ix_audit_events_runner_id", "audit_events", ["runner_id"])
    op.create_table(
        "console_chunks",
        Column("seq", Integer(), nullable=False),
        Column("run_id", String(), nullable=False),
        Column("offset", Integer(), nullable=False),
        Column("end", Integer(), nullable=False),
        Column("data", LargeBinary(), nullable=False),
        Column("at", Integer(), nullable=False),
        PrimaryKeyConstraint("seq"),
        UniqueConstraint("run_id", "offset"),
    )
    op.create_index("ix_console_chunks_run_id", "console_chunks", ["run_id"])
    op.create_table(
        "console_sizes",
        Column("run_id", String(), nullable=False),
        Column("cols", Integer(), nullable=False),
        Column("rows", Integer(), nullable=False),
        Column("owner", String(), nullable=False),
        Column("view", String(), nullable=False),
        Column("at", Integer(), nullable=False),
        PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "images",
        Column("id", String(), nullable=False),
        Column("version", String(), nullable=False),
        Column("digest", String(), nullable=False),
        Column("url", String(), nullable=False),
        Column("name", String(), nullable=True),
        Column("size_bytes", Integer(), nullable=True),
        Column("built_at", Integer(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("digest"),
    )
    op.create_table(
        "leases",
        Column("id", String(), nullable=False),
        Column("runner_id", String(), nullable=False),
        Column("live_runner_id", String(), nullable=True),
        Column("acquired_at", Integer(), nullable=False),
        Column("expires_at", Integer(), nullable=False),
        Column("expired_at", Integer(), nullable=True),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("live_runner_id"),
    )
    op.create_index("ix_leases_runner_id", "leases", ["runner_id"])
    op.create_table(
        "mcp_servers",
        Column("id", String(), nullable=False),
        Column("name", String(), nullable=False),
        Column("url", String(), nullable=False),
        Column("credential", String(), nullable=True),
        Column("timeout_seconds", Integer(), nullable=False),
        Column("max_calls_per_minute", Integer(), nullable=False),
        Column("disabled_at", Integer(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        Column("updated_at", Integer(), nullable=False),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("name"),
    )
    op.create_table(
        "merges",
        Column("run_id", String(), nullable=False),
        Column("entries", JSON(), nullable=False),
        Column("decision", JSON(), nullable=True),
        Column("conflicts", JSON(), nullable=True),
        Column("report", JSON(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        Column("updated_at", Integer(), nullable=False),
        PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "policies",
        Column("id", String(), nullable=False),
        Column("kind", POLICY_KIND, nullable=False),
        Column("name", String(), nullable=True),
        Column("digest", String(), nullable=False),
        Column("document", JSON(), nullable=False),
        Column("created_at", Integer(), nullable=False),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("kind", "digest"),
        UniqueConstraint("kind", "name"),
    )
    op.create_table(
        "profiles",
        Column("id", String(), nullable=False),
        Column("name", String(), nullable=False),
        Column("spec", JSON(), nullable=False),
        Column("digest", String(), nullable=False),
        Column("created_at", Integer(), nullable=False),
        Column("updated_at", Integer(), nullable=False),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("name"),
    )
    op.create_table(
        "run_policies",
        Column("run_id", String(), nullable=False),
        Column("seq", Integer(), nullable=False),
        Column("kind", POLICY_KIND, nullable=False),
        Column("policy_id", String(), nullable=True),
        Column("previous_id", String(), nullable=True),
        Column("document", JSON(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        PrimaryKeyConstraint("run_id", "seq"),
    )
    op.create_table(
        "runs",
        Column("id", String(), nullable=False),
        Column("seq", Integer(), nullable=False),
        Column("status", RUN_STATUS, nullable=False),
        Column("status_reason", String(), nullable=True),
        Column("spec", JSON(), nullable=False),
        Column("mount_policy_id", String(), nullable=True),
        Column("network_policy_id", String(), nullable=True),
        Column("shell_policy_id", String(), nullable=True),
        Column("mcp_policy_id", String(), nullable=True),
        Column("mcp_document", JSON(), nullable=True),
        Column("model_policy_id", String(), nullable=True),
        Column("profile_id", String(), nullable=True),
        Column("runner_id", String(), nullable=True),
        Column("lease_id", String(), nullable=True),
        Column("idempotency_key", String(), nullable=False),
        Column("request_digest", String(), nullable=False),
        Column("created_at", Integer(), nullable=False),
        Column("updated_at", Integer(), nullable=False),
        Column("started_at", Integer(), nullable=True),
        Column("finished_at", Integer(), nullable=True),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("seq"),
        UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_profile_id", "runs", ["profile_id"])
    op.create_index("ix_runs_runner_id", "runs", ["runner_id"])
    op.create_index("ix_runs_lease_id", "runs", ["lease_id"])
    op.create_table(
        "secrets",
        Column("id", String(), nullable=False),
        Column("name", String(), nullable=False),
        Column("expires_at", Integer(), nullable=True),
        Column("created_at", Integer(), nullable=False),
        Column("rotated_at", Integer(), nullable=True),
        Column("value", String(), nullable=False),
        PrimaryKeyConstraint("id"),
        UniqueConstraint("name"),
    )


def downgrade() -> None:
    for table in (
        "secrets",
        "runs",
        "run_policies",
        "profiles",
        "policies",
        "merges",
        "mcp_servers",
        "leases",
        "images",
        "console_sizes",
        "console_chunks",
        "audit_events",
        "agents",
    ):
        op.drop_table(table)
    # Dropping a table leaves a PostgreSQL enum type behind; elsewhere these are no-ops.
    RUN_STATUS.drop(op.get_bind(), checkfirst=True)
    POLICY_KIND.drop(op.get_bind(), checkfirst=True)
