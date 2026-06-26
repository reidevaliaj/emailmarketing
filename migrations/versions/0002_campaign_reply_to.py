"""add campaigns.reply_to

Revision ID: 0002_reply_to
Revises: 0001_initial
Create Date: 2026-06-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_reply_to"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("reply_to", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "reply_to")
