"""@mentions on notes, and each member's notifications.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_message ADD CONSTRAINT hd_message_workspace_id_id UNIQUE (workspace_id, id)"
    )
    op.execute(
        """
        CREATE TABLE hd_mention (
            workspace_id  uuid NOT NULL,
            message_id    uuid NOT NULL,
            member_id     uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (message_id, member_id),
            FOREIGN KEY (workspace_id, message_id)
                REFERENCES hd_message (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_notification (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL,
            member_id     uuid NOT NULL,
            ticket_id     uuid,
            kind          text NOT NULL CHECK (kind IN ('mention')),
            text          text NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
            read_at       timestamptz,
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_notification_inbox "
        "ON hd_notification (workspace_id, member_id, read_at, created_at DESC)"
    )
    for table in ("hd_mention", "hd_notification"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_notification")
    op.execute("DROP TABLE hd_mention")
    op.execute("ALTER TABLE hd_message DROP CONSTRAINT hd_message_workspace_id_id")
