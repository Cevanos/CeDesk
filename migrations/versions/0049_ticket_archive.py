"""The archive tier (md O3).

- `hd_ticket_archive`: a resolved ticket older than the workspace's archive age (default
  120 days), moved out of `hd_ticket` by the worker so the main tables stay fast. One row
  per ticket with everything that was on it, so nothing is lost: the ticket as it was
  (`ticket`, with its team's and assignee's names then), every message with all its
  columns (`messages`), its files' names, sizes and storage keys (`files`, so retention
  and erase can delete the files; `file_bytes` their total, for storage use), its rating,
  and the rest of its history (`related`: time logged, tasks, approvals, links, side
  conversations with their messages, Jira issues, emails sent, the Cevanos chat).
  Found by number, customer, import source id (`source`, `source_id`) or an email's
  Message-ID (`email_ids`, for a late reply); read only.
- `hd_ticket_merged_into`: an index for the worker's "still needed by a live ticket" check.
- `hd_retention.archive_after_days`: the Admin's archive age (NULL: 120 days).

Downgrade refuses while any ticket is archived (dropping the table would lose them).

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence

import sqlalchemy as sa
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
            source_id     text,
            contact_id    uuid,
            team_id       uuid,
            assignee_id   uuid,
            created_at    timestamptz NOT NULL,
            resolved_at   timestamptz,
            archived_at   timestamptz NOT NULL DEFAULT now(),
            ticket        jsonb NOT NULL CHECK (jsonb_typeof(ticket) = 'object'),
            messages      jsonb NOT NULL CHECK (jsonb_typeof(messages) = 'array'),
            files         jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(files) = 'array'),
            file_bytes    bigint NOT NULL DEFAULT 0 CHECK (file_bytes >= 0),
            rating        jsonb,
            related       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(related) = 'object'),
            email_ids     text[] NOT NULL DEFAULT '{}',
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
    op.execute(
        "CREATE INDEX hd_ticket_archive_source ON hd_ticket_archive "
        "(workspace_id, source, source_id) WHERE source_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX hd_ticket_archive_email_ids ON hd_ticket_archive USING gin (email_ids)"
    )
    # The worker never archives a ticket another live one was merged into (or is a child
    # of: `hd_ticket_children`); this keeps that check an index lookup.
    op.execute(
        "CREATE INDEX hd_ticket_merged_into ON hd_ticket (workspace_id, merged_into_id) "
        "WHERE merged_into_id IS NOT NULL"
    )
    for statement in tenant_rls_sql("hd_ticket_archive"):
        op.execute(statement)


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it to count them.
    # Refused (and rolled back) while any ticket is archived: dropping the table would
    # lose them. Put them back or delete them on purpose first.
    op.execute("ALTER TABLE hd_ticket_archive NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM hd_ticket_archive")).scalar_one()
    if count:
        raise RuntimeError(
            f"hd_ticket_archive holds {count} archived tickets; downgrading 0049 would "
            "delete them. Empty the archive on purpose first (nothing was changed)."
        )
    op.execute("DROP TABLE hd_ticket_archive")
    op.execute("DROP INDEX hd_ticket_merged_into")
    op.execute("ALTER TABLE hd_retention DROP COLUMN archive_after_days")
