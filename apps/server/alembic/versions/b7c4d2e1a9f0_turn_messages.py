"""persist turn messages

Revision ID: b7c4d2e1a9f0
Revises: 9f3a2b1c4d5e
Create Date: 2026-11-16 20:05:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4d2e1a9f0"
down_revision: str | None = "9f3a2b1c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("messages_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("turns") as batch:
        batch.drop_column("messages_json")
