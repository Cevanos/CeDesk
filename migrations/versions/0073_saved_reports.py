"""Saved reports (feature-gap plan 3.2): a named report definition.

- `hd_saved_report`: the owner, a name, the definition (metric, group_by, filters and a
  *preset* range such as `last_30d`, so it stays current; checked by the service before
  saving) and whether it is shared. A personal report is seen by its owner only; a shared
  one by everyone who holds the reports right. Names are unique per owner among personal
  reports and per workspace among shared ones (ignoring case).
- The owner's reports go when the member row is deleted.

Revision ID: 0073
Revises: 0072
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_saved_report (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            owner_id      uuid NOT NULL,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            definition    jsonb NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
            shared        boolean NOT NULL DEFAULT false,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_saved_report_personal_name ON hd_saved_report "
        "(workspace_id, owner_id, lower(name)) WHERE NOT shared"
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_saved_report_shared_name ON hd_saved_report "
        "(workspace_id, lower(name)) WHERE shared"
    )
    op.execute("CREATE INDEX hd_saved_report_owner ON hd_saved_report (workspace_id, owner_id)")
    for statement in tenant_rls_sql("hd_saved_report"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_saved_report")
