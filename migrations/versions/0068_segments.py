"""Segments (feature-gap plan 2.6): saved contact-list filters, shared by the workspace.

- `hd_segment`: a name (unique per workspace, ignoring case) and the filters (the same
  keys `GET /contacts` takes, checked by the service before saving). Who is in a segment
  is worked out when it is read, never stored, so there is nothing to erase per customer.
  A separate table from `hd_saved_view` (plan 1.2): views hold ticket-list filters and
  can be personal; a segment is a workspace-wide list of customers (a later campaign's
  recipients).

Revision ID: 0068
Revises: 0067
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_segment (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            filters       jsonb NOT NULL CHECK (jsonb_typeof(filters) = 'object'),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX hd_segment_name ON hd_segment (workspace_id, lower(name))")
    for statement in tenant_rls_sql("hd_segment"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_segment")
