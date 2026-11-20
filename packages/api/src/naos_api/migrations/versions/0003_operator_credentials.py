"""Operator requests are signed with credentials whose generation lives here.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
from sqlmodel import Column, Integer, PrimaryKeyConstraint, String

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "operator_credentials",
        Column("id", String(), nullable=False),
        Column("generation", Integer(), nullable=False),
        Column("updated_at", Integer(), nullable=False),
        PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("operator_credentials")
