"""add article generation flags and asset blob

Revision ID: 8b482363487c
Revises: 005f8478a5cb
Create Date: 2025-10-11 23:05:57.069614

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b482363487c'
down_revision: Union[str, Sequence[str], None] = '005f8478a5cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
     # --- Article flags (checkmarks) ---
    op.add_column(
        "articles",
        sa.Column("has_image_generated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "articles",
        sa.Column("image_generated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "articles",
        sa.Column("has_text_generated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "articles",
        sa.Column("text_generated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_articles_has_image_generated", "articles", ["has_image_generated"])
    op.create_index("ix_articles_has_text_generated", "articles", ["has_text_generated"])

    # --- Optional: store image bytes in DB as well ---
    op.add_column("assets", sa.Column("data", sa.LargeBinary(), nullable=True))
    op.add_column("assets", sa.Column("size_bytes", sa.Integer(), nullable=True))
    pass


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_articles_has_text_generated", table_name="articles")
    op.drop_index("ix_articles_has_image_generated", table_name="articles")

    op.drop_column("articles", "text_generated_at")
    op.drop_column("articles", "has_text_generated")
    op.drop_column("articles", "image_generated_at")
    op.drop_column("articles", "has_image_generated")

    op.drop_column("assets", "size_bytes")
    op.drop_column("assets", "data")
    pass
