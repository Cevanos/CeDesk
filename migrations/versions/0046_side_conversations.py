"""Side conversations (md X5).

- `hd_side_conversation`: an email thread with another team or supplier, on a ticket.
- `hd_side_message`: each email in it, sent (`out`) or received (`in`), with its
  Message-ID, which is how a reply finds its way back (unique per workspace, so the same
  email is saved once).
- Notification kind `side_reply`: the ticket's assignee hears about a reply.
- Neither table is ever read by the customer portal or a customer email.

Revision ID: 0046
Revises: 0045
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_side_conversation", "hd_side_message")
KINDS = (
    "'mention', 'assigned', 'customer_reply', 'sla_breach', 'approval', 'task', "
    "'task_overdue', 'child_resolved'"
)


def _kinds(allowed: str) -> None:
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({allowed}))"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_side_conversation (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id        uuid NOT NULL,
            subject          text NOT NULL CHECK (length(subject) BETWEEN 1 AND 250),
            to_addresses     text[] NOT NULL CHECK (cardinality(to_addresses) BETWEEN 1 AND 10),
            from_address     text NOT NULL CHECK (length(from_address) <= 254),
            created_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            last_message_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_side_conversation_ticket ON hd_side_conversation "
        "(workspace_id, ticket_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE hd_side_message (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            side_id            uuid NOT NULL,
            direction          text NOT NULL CHECK (direction IN ('out', 'in')),
            body               text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
            author_member_id   uuid,
            author_name        text NOT NULL CHECK (length(author_name) <= 120),
            from_address       text NOT NULL CHECK (length(from_address) <= 254),
            header_message_id  text NOT NULL CHECK (length(header_message_id) <= 200),
            created_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (workspace_id, header_message_id),
            FOREIGN KEY (workspace_id, side_id)
                REFERENCES hd_side_conversation (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, author_member_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (author_member_id)
        )
        """
    )
    op.execute("CREATE INDEX hd_side_message_side ON hd_side_message (side_id, created_at)")
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    _kinds(f"{KINDS}, 'side_reply'")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_notification WHERE kind = 'side_reply'")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    _kinds(KINDS)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
