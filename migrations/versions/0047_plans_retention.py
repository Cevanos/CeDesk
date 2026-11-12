"""Cloud plans and retention (md O2, O4).

- `hd_workspace_plan`: the cloud plan our operators gave a workspace (`cedesk set-plan`).
  No row: no limits. The plans' numbers are config (`CEDESK_PLANS`), not data.
- `hd_retention`: the Admin's "delete resolved tickets after N days" (NULL: keep).

Revision ID: 0047
Revises: 0046
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_workspace_plan", "hd_retention")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_workspace_plan (
            workspace_id  uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            plan          text NOT NULL CHECK (plan ~ '^[a-z][a-z0-9_-]{0,39}$'),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_retention (
            workspace_id        uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            closed_ticket_days  integer CHECK (closed_ticket_days BETWEEN 30 AND 3650),
            updated_by          uuid,
            updated_at          timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
