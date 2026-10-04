"""The schema the models defined when Alembic took it over.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

RUN_STATUS = sa.Enum(
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
POLICY_KIND = sa.Enum("MOUNT", "NETWORK", "SHELL", "MCP", "MODEL", name="policykind")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("token_expires_at", sa.Integer(), nullable=False),
        sa.Column("prev_token_hash", sa.String(), nullable=True),
        sa.Column("prev_token_expires_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_heartbeat_at", sa.Integer(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.Column("drained_at", sa.Integer(), nullable=True),
        sa.Column("host", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("zone", sa.String(), nullable=True),
        sa.Column("platform", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("heartbeat_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.UniqueConstraint("prev_token_hash"),
    )
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("at", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("vm_id", sa.String(), nullable=True),
        sa.Column("runner_id", sa.String(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id"),
    )
    op.create_index("ix_audit_events_event", "audit_events", ["event"])
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])
    op.create_index("ix_audit_events_runner_id", "audit_events", ["runner_id"])
    op.create_table(
        "console_chunks",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("run_id", "offset"),
    )
    op.create_index("ix_console_chunks_run_id", "console_chunks", ["run_id"])
    op.create_table(
        "console_sizes",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("cols", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("view", sa.String(), nullable=False),
        sa.Column("at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("built_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
    )
    op.create_table(
        "leases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("runner_id", sa.String(), nullable=False),
        sa.Column("live_runner_id", sa.String(), nullable=True),
        sa.Column("acquired_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("expired_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("live_runner_id"),
    )
    op.create_index("ix_leases_runner_id", "leases", ["runner_id"])
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("credential", sa.String(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_calls_per_minute", sa.Integer(), nullable=False),
        sa.Column("disabled_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "merges",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=True),
        sa.Column("conflicts", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", POLICY_KIND, nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "digest"),
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", RUN_STATUS, nullable=False),
        sa.Column("status_reason", sa.String(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("mount_policy_id", sa.String(), nullable=True),
        sa.Column("network_policy_id", sa.String(), nullable=True),
        sa.Column("shell_policy_id", sa.String(), nullable=True),
        sa.Column("mcp_policy_id", sa.String(), nullable=True),
        sa.Column("mcp_document", sa.JSON(), nullable=True),
        sa.Column("model_policy_id", sa.String(), nullable=True),
        sa.Column("profile_id", sa.String(), nullable=True),
        sa.Column("runner_id", sa.String(), nullable=True),
        sa.Column("lease_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_profile_id", "runs", ["profile_id"])
    op.create_index("ix_runs_runner_id", "runs", ["runner_id"])
    op.create_index("ix_runs_lease_id", "runs", ["lease_id"])
    op.create_table(
        "secrets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("rotated_at", sa.Integer(), nullable=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    for table in (
        "secrets",
        "runs",
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
