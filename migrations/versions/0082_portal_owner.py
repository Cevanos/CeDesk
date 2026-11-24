"""Customer resolution portal (differentiator plan #9): the portal shows who owns a
ticket. `hd_portal_settings.show_owner` (on by default): the assignee's first name; off:
the team's name only. Never an email address.

Revision ID: 0082
Revises: 0081
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_portal_settings ADD COLUMN show_owner boolean NOT NULL DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_portal_settings DROP COLUMN show_owner")
