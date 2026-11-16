"""add proposal reason

Revision ID: 9f3a2b1c4d5e
Revises: e8f6eeecce72
Create Date: 2026-11-16 19:50:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3a2b1c4d5e"
down_revision: str | None = "e8f6eeecce72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("reason", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_column("reason")
