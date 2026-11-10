"""Email out (md C9, 7.4): a workspace's support addresses, and the queue of emails to send.

`hd_email_address`: the addresses customers write to and replies come from, each with a
display name and a signature. An address belongs to one workspace (incoming email is matched
to its workspace by address). One may be the default.

`hd_email_out`: one row per email to send. It is written in the same transaction as the
reply, so a reply is never lost or sent twice by a rollback; `cedesk worker` sends it and
retries with a growing wait.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_email_address (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            address       text NOT NULL
                          CHECK (address = lower(address) AND length(address) BETWEEN 3 AND 254),
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            signature     text NOT NULL DEFAULT '' CHECK (length(signature) <= 2000),
            is_default    boolean NOT NULL DEFAULT false,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (address),
            UNIQUE (workspace_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_email_address_one_default ON hd_email_address (workspace_id) "
        "WHERE is_default"
    )
    for statement in tenant_rls_sql("hd_email_address"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE hd_email_out (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id        uuid,
            message_id       uuid,
            from_address     text NOT NULL,
            from_name        text NOT NULL,
            to_addresses     text[] NOT NULL CHECK (cardinality(to_addresses) BETWEEN 1 AND 20),
            cc_addresses     text[] NOT NULL DEFAULT '{}'
                             CHECK (cardinality(cc_addresses) <= 20),
            subject          text NOT NULL,
            body_text        text NOT NULL,
            header_message_id text,
            in_reply_to      text,
            references_ids   text[] NOT NULL DEFAULT '{}',
            status           text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'sent', 'failed')),
            attempts         integer NOT NULL DEFAULT 0,
            next_attempt_at  timestamptz NOT NULL DEFAULT now(),
            last_error       text CHECK (length(last_error) <= 500),
            sent_at          timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, message_id)
                REFERENCES hd_message (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_email_out_due ON hd_email_out (workspace_id, next_attempt_at) "
        "WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX hd_email_out_message ON hd_email_out (message_id)")
    for statement in tenant_rls_sql("hd_email_out"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_email_out")
    op.execute("DROP TABLE hd_email_address")
