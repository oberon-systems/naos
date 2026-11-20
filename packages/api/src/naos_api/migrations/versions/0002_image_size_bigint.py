"""An agent image is several GiB, past what a 32-bit size_bytes column holds.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
from sqlmodel import BigInteger, Integer

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("images") as batch:
        batch.alter_column(
            "size_bytes", type_=BigInteger(), existing_type=Integer(), existing_nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("images") as batch:
        batch.alter_column(
            "size_bytes", type_=Integer(), existing_type=BigInteger(), existing_nullable=True
        )
