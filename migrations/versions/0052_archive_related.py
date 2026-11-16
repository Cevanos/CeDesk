"""The archive keeps everything (md O3, from the review of 0049).

0049 already ran on servers, so what the review added to the archive comes here:

- `hd_ticket_archive.related`: the rest of a ticket's history (time logged, tasks,
  approvals, links, side conversations with their messages, Jira issues, emails sent,
  the Cevanos chat), so nothing is lost when a ticket is archived.
- `file_bytes`: the total size of its files, so storage use counts archived files.
- `source_id`: the import source's id, so a re-import skips tickets already archived.
- `email_ids`: its emails' Message-IDs, so a late reply to an archived ticket opens a
  follow-up ticket.
- Indexes for those lookups, and `hd_ticket_merged_into` for the worker's "still needed
  by a live ticket" check.

Tickets archived before this (by 0049's first version) get `source_id` and `file_bytes`
from what they kept; their `related` and `email_ids` start empty (that version didn't keep
them).

Downgrade refuses while any ticket is archived (it would drop their history).

Revision ID: 0052
Revises: 0051
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE hd_ticket_archive
            ADD COLUMN source_id   text,
            ADD COLUMN file_bytes  bigint NOT NULL DEFAULT 0 CHECK (file_bytes >= 0),
            ADD COLUMN related     jsonb NOT NULL DEFAULT '{}'
                                   CHECK (jsonb_typeof(related) = 'object'),
            ADD COLUMN email_ids   text[] NOT NULL DEFAULT '{}'
        """
    )
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the update.
    op.execute("ALTER TABLE hd_ticket_archive NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE hd_ticket_archive a SET
            source_id = a.ticket->>'source_id',
            file_bytes = coalesce((SELECT sum((f->>'size')::bigint)
                                   FROM jsonb_array_elements(a.files) f), 0)
        """
    )
    op.execute("ALTER TABLE hd_ticket_archive FORCE ROW LEVEL SECURITY")
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


def downgrade() -> None:
    # Refused (and rolled back) while any ticket is archived: dropping `related` would lose
    # their history. Put them back or delete them on purpose first.
    op.execute("ALTER TABLE hd_ticket_archive NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM hd_ticket_archive")).scalar_one()
    op.execute("ALTER TABLE hd_ticket_archive FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(
            f"hd_ticket_archive holds {count} archived tickets; downgrading 0052 would "
            "delete their history. Empty the archive on purpose first (nothing was changed)."
        )
    op.execute("DROP INDEX hd_ticket_merged_into")
    op.execute("DROP INDEX hd_ticket_archive_email_ids")
    op.execute("DROP INDEX hd_ticket_archive_source")
    op.execute(
        """
        ALTER TABLE hd_ticket_archive
            DROP COLUMN email_ids,
            DROP COLUMN related,
            DROP COLUMN file_bytes,
            DROP COLUMN source_id
        """
    )
