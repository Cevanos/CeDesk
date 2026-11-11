"""Web form channel (md C2): the Admin turns the public contact form on (off by default,
so a workspace that doesn't use it has no open public form).

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_portal_settings ADD COLUMN web_form boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_portal_settings DROP COLUMN web_form")
