"""Each agent's routing state: status, channels, capacity per channel and skills.

A member without a row uses the defaults (available, every channel, Email 5 /
Chat 2 / Messaging 3, no skills), so new agents can be routed to at once.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_agent_state (
            workspace_id   uuid NOT NULL,
            member_id      uuid NOT NULL,
            status         text NOT NULL DEFAULT 'available'
                           CHECK (status IN ('available', 'on_break', 'transfers_only', 'offline')),
            channels       text[] NOT NULL DEFAULT '{email,chat,messaging}'
                           CHECK (channels <@ ARRAY['email', 'chat', 'messaging']),
            cap_email      integer NOT NULL DEFAULT 5 CHECK (cap_email BETWEEN 0 AND 100),
            cap_chat       integer NOT NULL DEFAULT 2 CHECK (cap_chat BETWEEN 0 AND 100),
            cap_messaging  integer NOT NULL DEFAULT 3 CHECK (cap_messaging BETWEEN 0 AND 100),
            skills         text[] NOT NULL DEFAULT '{}',
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, member_id),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    for statement in tenant_rls_sql("hd_agent_state"):
        op.execute(statement)
    # Round-robin order within a team: the order the Admin listed the members.
    op.execute("ALTER TABLE hd_team_member ADD COLUMN position integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        "CHECK (kind IN ('mention', 'assigned'))"
    )
    op.execute(
        "CREATE INDEX hd_ticket_open_by_assignee ON hd_ticket (workspace_id, assignee_id, channel) "
        "WHERE status <> 'resolved'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_ticket_open_by_assignee")
    # Forced RLS applies to the owner too: without a workspace set, the DELETE
    # would see no rows. Lift it for this one statement.
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_notification WHERE kind = 'assigned'")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        "CHECK (kind IN ('mention'))"
    )
    op.execute("ALTER TABLE hd_team_member DROP COLUMN position")
    op.execute("DROP TABLE hd_agent_state")
