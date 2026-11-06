"""The app role may read the migration marker but never change it.

`alembic_version` is created by the owner, so the owner's default privileges
granted `cedesk_app` write access to it like any other table.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM cedesk_app")


def downgrade() -> None:
    op.execute("GRANT INSERT, UPDATE, DELETE ON alembic_version TO cedesk_app")
