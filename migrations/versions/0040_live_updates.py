"""Live updates (md C11, P7): the event log a stream reads, and the tickets that open one.

- `hd_event_log`: what an open stream is sent. One row per event: `kind`, the ticket it is
  about (and that ticket's team when the row was written, so a role limited to one team's
  tickets can be filtered cheaply), who it is for (everyone with access to the ticket, or one
  member, or one portal customer) and a payload of ids only: never a subject, a message text
  or an address. `seq` is the event's number, which a client sends back as `Last-Event-ID`
  to carry on without losing one. Rows are written in the same transaction as the change they
  describe and kept 24 hours (the worker prunes them).
- `hd_stream_ticket`: a browser's EventSource can't send an Authorization header, so a signed-in
  client asks for a ticket first and opens the stream with it. Only the SHA-256 of the ticket is
  stored; it lasts a minute and works once.

Revision ID: 0040
Revises: 0039
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_event_log", "hd_stream_ticket")


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE hd_event_log (
            seq                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            workspace_id       uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            kind               text NOT NULL
                               CHECK (kind ~ '^[a-z]+(\.[a-z_]+)+$' AND length(kind) <= 60),
            ticket_id          uuid,
            team_id            uuid,
            target_member_id   uuid,
            target_contact_id  uuid,
            payload            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
            -- clock_timestamp, not now(): events written in one transaction keep their order.
            created_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (target_member_id IS NULL OR target_contact_id IS NULL)
        )
        """
    )
    op.execute("CREATE INDEX hd_event_log_ws_seq ON hd_event_log (workspace_id, seq)")
    op.execute(
        "CREATE INDEX hd_event_log_member ON hd_event_log (workspace_id, target_member_id, seq) "
        "WHERE target_member_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX hd_event_log_contact ON hd_event_log (workspace_id, target_contact_id, seq) "
        "WHERE target_contact_id IS NOT NULL"
    )
    op.execute("CREATE INDEX hd_event_log_created ON hd_event_log (created_at)")
    op.execute(
        """
        CREATE TABLE hd_stream_ticket (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            token_hash    bytea NOT NULL UNIQUE,
            audience      text NOT NULL CHECK (audience IN ('agent', 'customer')),
            session_id    uuid NOT NULL,
            member_id     uuid,
            contact_id    uuid,
            expires_at    timestamptz NOT NULL,
            used_at       timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (
                (audience = 'agent' AND member_id IS NOT NULL AND contact_id IS NULL)
                OR (audience = 'customer' AND contact_id IS NOT NULL AND member_id IS NULL)
            )
        )
        """
    )
    op.execute("CREATE INDEX hd_stream_ticket_expires ON hd_stream_ticket (expires_at)")
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
