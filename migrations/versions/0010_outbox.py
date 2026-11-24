"""The outbox (md 3.2 step 5): events written in the same transaction as the change.

A worker (the outbox queue) reads unpublished rows after the commit and
turns each into webhooks, emails, notifications and so on, with an idempotency key per
effect. So an event exists exactly when its change was committed.

`ticket_id` is a plain column, not a foreign key: an event outlives what it describes.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_outbox (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            seq           bigint GENERATED ALWAYS AS IDENTITY,
            event         text NOT NULL CHECK (event IN ('ticket.created', 'ticket.updated',
                              'ticket.resolved', 'message.created', 'assignment.changed')),
            ticket_id     uuid,
            payload       jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            occurred_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
            published_at  timestamptz,
            attempts      integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            UNIQUE (seq)
        )
        """
    )
    # What the worker reads: unpublished events, oldest first.
    op.execute(
        "CREATE INDEX hd_outbox_pending ON hd_outbox (workspace_id, seq) WHERE published_at IS NULL"
    )
    op.execute("CREATE INDEX hd_outbox_ticket ON hd_outbox (workspace_id, ticket_id, seq)")
    for statement in tenant_rls_sql("hd_outbox"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_outbox")
