"""Outgoing webhooks (md PL3-PL6).

- `hd_webhook`: an Admin's URL, the events it listens to and its secret, sealed (AES-GCM,
  `cedesk.crypto`, bound to the workspace and the webhook) so the worker can sign with it;
  only `secret_hint` is ever shown again.
- `hd_webhook_delivery`: one per (webhook, outbox event), made when the event is handed
  out, so each URL is tried, retried and logged on its own. `body` is exactly what is sent.
  The test ping has no event.
- `hd_webhook_attempt`: every try (when, the answer's status, how long, the error).

Revision ID: 0043
Revises: 0042
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_webhook", "hd_webhook_delivery", "hd_webhook_attempt")
_EVENTS = "'ticket.created', 'ticket.updated', 'ticket.resolved', 'message.created'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE hd_webhook (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            url             text NOT NULL CHECK (url LIKE 'https://%' AND length(url) <= 500),
            events          text[] NOT NULL
                            CHECK (cardinality(events) >= 1 AND events <@ ARRAY[{_EVENTS}]),
            secret_sealed   bytea NOT NULL,
            secret_hint     text NOT NULL CHECK (length(secret_hint) <= 20),
            enabled         boolean NOT NULL DEFAULT true,
            created_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_webhook_delivery (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            webhook_id      uuid NOT NULL,
            event_id        uuid,
            event           text NOT NULL CHECK (length(event) <= 60),
            body            jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
            state           text NOT NULL DEFAULT 'pending'
                            CHECK (state IN ('pending', 'delivered', 'failed')),
            tries           integer NOT NULL DEFAULT 0 CHECK (tries >= 0),
            next_try_at     timestamptz NOT NULL DEFAULT now(),
            lease_until     timestamptz,
            last_status     integer,
            last_ms         integer,
            last_error      text CHECK (length(last_error) <= 300),
            delivered_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (workspace_id, id),
            UNIQUE (webhook_id, event_id),
            FOREIGN KEY (workspace_id, webhook_id)
                REFERENCES hd_webhook (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_webhook_delivery_due ON hd_webhook_delivery (next_try_at) "
        "WHERE state = 'pending'"
    )
    op.execute(
        "CREATE INDEX hd_webhook_delivery_recent "
        "ON hd_webhook_delivery (workspace_id, created_at DESC, id DESC)"
    )
    op.execute(
        """
        CREATE TABLE hd_webhook_attempt (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            delivery_id     uuid NOT NULL,
            at              timestamptz NOT NULL DEFAULT clock_timestamp(),
            status          integer,
            ms              integer NOT NULL CHECK (ms >= 0),
            error           text CHECK (length(error) <= 300),
            response        text CHECK (length(response) <= 300),
            FOREIGN KEY (workspace_id, delivery_id)
                REFERENCES hd_webhook_delivery (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_webhook_attempt_delivery ON hd_webhook_attempt (delivery_id, at)")
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
