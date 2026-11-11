"""Ticket types customers can pick (md P3, C2): `for_customers` hides an internal type
(e.g. "Internal escalation") from the portal and the web form. On by default, so the
existing types stay as they were.

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_ticket_type ADD COLUMN for_customers boolean NOT NULL DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_ticket_type DROP COLUMN for_customers")
