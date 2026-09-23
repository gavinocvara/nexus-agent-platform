"""Create the Orders service schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the orders table and user lookup index."""

    op.create_table(
        "orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"], unique=False)


def downgrade() -> None:
    """Remove the orders schema."""

    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")
