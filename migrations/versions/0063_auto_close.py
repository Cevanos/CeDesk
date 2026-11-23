"""Auto-close inactive tickets (feature-gap plan 1.4): the workspace's setting.

- `hd_auto_close`: one row per workspace once an Admin saves it. Off by default (no row,
  or `enabled` false). `after_days`: a Pending ticket with no reply from the customer for
  this many days is resolved by the daily pass. `notice`: an optional message to the
  customer, added to the ticket as a reply (and emailed like one) before it is closed.

Revision ID: 0063
Revises: 0062
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_auto_close (
            workspace_id  uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            enabled       boolean NOT NULL DEFAULT false,
            after_days    integer NOT NULL DEFAULT 7 CHECK (after_days BETWEEN 1 AND 365),
            notice        text CHECK (length(notice) BETWEEN 1 AND 5000),
            updated_by    uuid,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_auto_close"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_auto_close")
