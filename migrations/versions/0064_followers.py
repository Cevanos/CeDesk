"""Ticket followers (feature-gap plan 1.5).

- `hd_ticket_follower`: a member who hears (notification kind `following`, added in 0061)
  about a ticket's public replies and status changes. One row per ticket and member.
  `added_by`: who added them (null: they followed it themselves, or were added
  automatically when assigned or @mentioned).
- Goes with the ticket (also when it moves to the archive: following is an inbox
  preference, not the ticket's history) and with the member.

Revision ID: 0064
Revises: 0063
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_ticket_follower (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            member_id     uuid NOT NULL,
            added_by      uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, ticket_id, member_id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, added_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (added_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_ticket_follower_member ON hd_ticket_follower (workspace_id, member_id)"
    )
    for statement in tenant_rls_sql("hd_ticket_follower"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_ticket_follower")
