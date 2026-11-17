"""Imports: what the source last showed for each imported ticket (audit P1-10).

`hd_import_seen` keeps, per imported ticket, the values the source gave the last time it
was read (status, priority, assignee, group, tags, subject, custom fields), as CeDesk maps
them. A sync look or a re-run changes a CeDesk field only when the source's value changed
since then, so an agent's edit in CeDesk is no longer reverted by the next look (before, a
status or priority set in CeDesk went back within 5 minutes). One row per ticket; it goes
with the ticket.

Downgrade drops the table: sync then falls back to the old rule (the source's values win),
nothing imported is touched.

Revision ID: 0057
Revises: 0056
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_import_seen (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            seen          jsonb NOT NULL CHECK (jsonb_typeof(seen) = 'object'),
            seen_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, ticket_id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    for statement in tenant_rls_sql("hd_import_seen"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_import_seen")
