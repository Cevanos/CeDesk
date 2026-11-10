"""Outbox delivery and idempotency keys (md 3.2 step 7, 2B "Idempotency", "Reliability").

Outbox delivery: the worker hands each committed event to the handlers that want it (the
Cevanos connector next; webhooks in Phase 3), one ticket's events in order.
- `hd_outbox.next_attempt_at`, `last_error`, `failed_at`: retries with backoff; after
  the last one the event is failed (the dead-letter list Admins see and can retry).
- `hd_outbox_done`: one row per (event, handler) that finished, so a retry never repeats
  an effect that already happened. `event_id:handler` is the idempotency key a handler
  passes on to the outside service.

`hd_idempotency`: the `Idempotency-Key` a member sent with a POST that creates something,
saved in the same transaction as what it created. A repeat of the request returns that
thing instead of making a second one; the same key with a different request is refused.
Keys are kept 24 hours.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_outbox "
        "ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now(), "
        "ADD COLUMN last_error text CHECK (length(last_error) <= 500), "
        "ADD COLUMN failed_at timestamptz, "
        "ADD CONSTRAINT hd_outbox_workspace_id_id_key UNIQUE (workspace_id, id)"
    )
    op.execute("DROP INDEX hd_outbox_pending")
    # What the worker reads: events still to deliver, due first.
    op.execute(
        "CREATE INDEX hd_outbox_due ON hd_outbox (workspace_id, next_attempt_at, seq) "
        "WHERE published_at IS NULL AND failed_at IS NULL"
    )
    op.execute(
        "CREATE INDEX hd_outbox_failed ON hd_outbox (workspace_id, failed_at) "
        "WHERE failed_at IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE hd_outbox_done (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            event_id      uuid NOT NULL,
            handler       text NOT NULL CHECK (handler ~ '^[a-z][a-z0-9_.-]{0,59}$'),
            done_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (event_id, handler),
            FOREIGN KEY (workspace_id, event_id)
                REFERENCES hd_outbox (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_idempotency (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL,
            key           text NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
            fingerprint   bytea NOT NULL CHECK (length(fingerprint) = 32),
            resource_id   uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, member_id, key),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_idempotency_age ON hd_idempotency (workspace_id, created_at)")
    for table in ("hd_outbox_done", "hd_idempotency"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_idempotency")
    op.execute("DROP TABLE hd_outbox_done")
    op.execute("DROP INDEX hd_outbox_failed")
    op.execute("DROP INDEX hd_outbox_due")
    op.execute(
        "CREATE INDEX hd_outbox_pending ON hd_outbox (workspace_id, seq) WHERE published_at IS NULL"
    )
    op.execute(
        "ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_workspace_id_id_key, "
        "DROP COLUMN failed_at, DROP COLUMN last_error, DROP COLUMN next_attempt_at"
    )
