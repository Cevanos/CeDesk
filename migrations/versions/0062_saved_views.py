"""Saved views (feature-gap plan 1.2): a named set of ticket-list filters.

- `hd_saved_view`: the owner, a name, the filters (the same keys `GET /tickets` takes:
  status, scope, priority, team, q, sort, imported, snoozed; checked by the service before
  saving) and whether it is shared. A personal view is seen by its owner only; a shared
  one by everyone in the workspace. Names are unique per owner among personal views, and
  per workspace among shared ones (ignoring case).
- The owner's views go when the member row is deleted.

Revision ID: 0062
Revises: 0061
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_saved_view (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            owner_id      uuid NOT NULL,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            filters       jsonb NOT NULL CHECK (jsonb_typeof(filters) = 'object'),
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
        "CREATE UNIQUE INDEX hd_saved_view_personal_name ON hd_saved_view "
        "(workspace_id, owner_id, lower(name)) WHERE NOT shared"
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_saved_view_shared_name ON hd_saved_view "
        "(workspace_id, lower(name)) WHERE shared"
    )
    op.execute("CREATE INDEX hd_saved_view_owner ON hd_saved_view (workspace_id, owner_id)")
    for statement in tenant_rls_sql("hd_saved_view"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_saved_view")
