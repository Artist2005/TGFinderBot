"""Initial schema for Price Tracker Bot

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-26 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("is_pro", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("pro_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("current_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="UAH", nullable=False),
        sa.Column("marketplace", sa.String(length=50), nullable=False),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(op.f("ix_products_url"), "products", ["url"], unique=True)
    op.create_index(op.f("ix_products_marketplace"), "products", ["marketplace"], unique=False)

    op.create_table(
        "user_products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("target_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_user_products_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_products_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_products")),
        sa.UniqueConstraint("user_id", "product_id", name="uq_user_products_user_product"),
    )
    op.create_index("ix_user_products_user_id", "user_products", ["user_id"], unique=False)
    op.create_index("ix_user_products_product_id", "user_products", ["product_id"], unique=False)

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_price_history_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_history")),
    )
    op.create_index(
        "ix_price_history_product_timestamp",
        "price_history",
        ["product_id", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_price_history_product_timestamp", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_user_products_product_id", table_name="user_products")
    op.drop_index("ix_user_products_user_id", table_name="user_products")
    op.drop_table("user_products")
    op.drop_index(op.f("ix_products_marketplace"), table_name="products")
    op.drop_index(op.f("ix_products_url"), table_name="products")
    op.drop_table("products")
    op.drop_table("users")
