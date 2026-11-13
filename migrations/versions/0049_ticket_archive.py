"""The archive tier (md O3).

- `hd_ticket_archive`: a resolved ticket older than the workspace's archive age (default
  120 days), moved out of `hd_ticket` by the worker so the main tables stay fast. One row
  per ticket with what opening it needs: the ticket as it was (`ticket`), its messages,
  its files' names and storage keys (so retention can delete the files) and its rating.
  Found by number or customer; read only.
- `hd_retention.archive_after_days`: the Admin's archive age (NULL: 120 days).

Downgrade puts nothing back: archived tickets are dropped with the table (as retention
would have deleted them later). Run it only where that is fine.

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_retention ADD COLUMN archive_after_days integer "
        "CHECK (archive_after_days BETWEEN 30 AND 3650)"
    )
    op.execute(
        """
        CREATE TABLE hd_ticket_archive (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            id            uuid NOT NULL,
            number        bigint NOT NULL,
            subject       text NOT NULL,
            status        text NOT NULL,
            priority      text NOT NULL,
            channel       text NOT NULL,
            source        text,
            contact_id    uuid,
            team_id       uuid,
            assignee_id   uuid,
            created_at    timestamptz NOT NULL,
            resolved_at   timestamptz,
            archived_at   timestamptz NOT NULL DEFAULT now(),
            ticket        jsonb NOT NULL CHECK (jsonb_typeof(ticket) = 'object'),
            messages      jsonb NOT NULL CHECK (jsonb_typeof(messages) = 'array'),
            files         jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(files) = 'array'),
            rating        jsonb,
            PRIMARY KEY (workspace_id, id),
            UNIQUE (workspace_id, number)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_ticket_archive_contact ON hd_ticket_archive (workspace_id, contact_id)"
    )
    op.execute(
        "CREATE INDEX hd_ticket_archive_resolved ON hd_ticket_archive (workspace_id, resolved_at)"
    )
    for statement in tenant_rls_sql("hd_ticket_archive"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_ticket_archive")
    op.execute("ALTER TABLE hd_retention DROP COLUMN archive_after_days")
