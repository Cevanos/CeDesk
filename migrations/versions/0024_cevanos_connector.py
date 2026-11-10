"""Cevanos connector (md C4-C7, section 7.1).

- `hd_cevanos_connection`: one per workspace. The `/v1` API key and the webhook signing
  secret, both sealed (AES-GCM, `cedesk.crypto`); the API shows only the key's last 4
  characters. `team_id`: the team handovers go to (then routing picks the agent, W8),
  unless a rule moves them. `close_on_resolve`: resolving a ticket closes the chat.
  Cevanos's address is a deployment setting (`CEDESK_CEVANOS_URL`), not stored here, so
  a workspace can't make the server call an address of its choosing.
- `hd_cevanos_chat`: one per handover ticket. Which Cevanos conversation and agent it
  is, the last chat message copied in (the polling watermark), when to poll next, when
  the take-over reached Cevanos, when the chat ended (then it is no longer read), and the
  last `conversation.escalated` event handled (a resent event is not a second handover).
- `hd_outbox` also accepts `handover.accepted` (an agent clicked Accept: the connector
  tells Cevanos to take the chat over).

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS_CHECK = (
    "ALTER TABLE hd_outbox ADD CONSTRAINT hd_outbox_event_check CHECK (event IN "
    "('ticket.created', 'ticket.updated', 'ticket.resolved', 'message.created', "
    "'assignment.changed', 'contact.updated'{}))"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_cevanos_connection (
            workspace_id           uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            api_key_sealed         bytea NOT NULL,
            api_key_hint           text NOT NULL CHECK (length(api_key_hint) <= 8),
            webhook_secret_sealed  bytea NOT NULL,
            close_on_resolve       boolean NOT NULL DEFAULT false,
            team_id                uuid,
            updated_by             uuid,
            created_at             timestamptz NOT NULL DEFAULT now(),
            updated_at             timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, team_id)
                REFERENCES hd_team (workspace_id, id) ON DELETE SET NULL (team_id),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_cevanos_chat (
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id        uuid NOT NULL,
            conversation_id  text NOT NULL CHECK (length(conversation_id) BETWEEN 1 AND 100),
            agent_id         text CHECK (length(agent_id) <= 100),
            channel_kind     text CHECK (length(channel_kind) <= 40),
            last_message_id  text CHECK (length(last_message_id) <= 100),
            next_poll_at     timestamptz NOT NULL DEFAULT now(),
            poll_failures    integer NOT NULL DEFAULT 0,
            taken_over_at    timestamptz,
            closed_at        timestamptz,
            handover_event_id text CHECK (length(handover_event_id) <= 100),
            created_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, ticket_id),
            UNIQUE (workspace_id, conversation_id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_cevanos_chat_poll ON hd_cevanos_chat (workspace_id, next_poll_at) "
        "WHERE closed_at IS NULL"
    )
    for table in ("hd_cevanos_connection", "hd_cevanos_chat"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(", 'handover.accepted'"))


def downgrade() -> None:
    op.execute("ALTER TABLE hd_outbox NO FORCE ROW LEVEL SECURITY")  # every workspace
    op.execute("DELETE FROM hd_outbox WHERE event = 'handover.accepted'")
    op.execute("ALTER TABLE hd_outbox FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(""))
    op.execute("DROP TABLE hd_cevanos_chat")
    op.execute("DROP TABLE hd_cevanos_connection")
