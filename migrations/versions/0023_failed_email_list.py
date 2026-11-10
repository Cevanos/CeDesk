"""The failed-email list for Admins (md 2B "Reliability": failed emails on the dead-letter
list): an index on the failed ones, newest first.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX hd_email_out_failed ON hd_email_out (workspace_id, created_at, id) "
        "WHERE status = 'failed'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_email_out_failed")
