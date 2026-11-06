"""Tickets, ticket types, teams, contacts, messages and the ticket number counter.

Every reference between workspace tables is a composite foreign key on
(workspace_id, id): a foreign-key check ignores RLS, so a single-column key
would accept a row id from another workspace.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "hd_counter",
    "hd_team",
    "hd_team_member",
    "hd_contact",
    "hd_ticket_type",
    "hd_ticket",
    "hd_message",
)


def upgrade() -> None:
    op.execute("ALTER TABLE member ADD CONSTRAINT member_workspace_id_id UNIQUE (workspace_id, id)")

    op.execute(
        """
        CREATE TABLE hd_counter (
            workspace_id        uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            next_ticket_number  integer NOT NULL DEFAULT 1001,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE hd_team (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id        uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name                text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            auto_assign         boolean NOT NULL DEFAULT true,
            max_open_per_agent  integer NOT NULL DEFAULT 5
                                CHECK (max_open_per_agent BETWEEN 1 AND 500),
            rr_pointer          uuid,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name),
            UNIQUE (workspace_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE hd_team_member (
            workspace_id  uuid NOT NULL,
            team_id       uuid NOT NULL,
            member_id     uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (team_id, member_id),
            FOREIGN KEY (workspace_id, team_id)
                REFERENCES hd_team (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_team_member_member ON hd_team_member (workspace_id, member_id)")

    # The customer. Holds the customer-card facts (T19) too: VIP and customer since.
    op.execute(
        """
        CREATE TABLE hd_contact (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            email           text NOT NULL
                            CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            vip             boolean NOT NULL DEFAULT false,
            customer_since  timestamptz NOT NULL DEFAULT now(),
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, email),
            UNIQUE (workspace_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE hd_ticket_type (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            fields          jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(fields) = 'array'),
            position        integer NOT NULL DEFAULT 0,
            required_skill  text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name),
            UNIQUE (workspace_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE hd_ticket (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            number          integer NOT NULL CHECK (number > 0),
            subject         text NOT NULL CHECK (length(subject) BETWEEN 1 AND 250),
            status          text NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'pending', 'resolved')),
            priority        text NOT NULL DEFAULT 'medium'
                            CHECK (priority IN ('urgent', 'high', 'medium', 'low')),
            type_id         uuid,
            team_id         uuid,
            assignee_id     uuid,
            contact_id      uuid NOT NULL,
            channel         text NOT NULL DEFAULT 'web'
                            CHECK (channel IN ('email', 'web', 'portal', 'api', 'chat', 'phone')),
            fields          jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(fields) = 'object'),
            tags            text[] NOT NULL DEFAULT '{}',
            created_by_id   uuid,
            first_reply_at  timestamptz,
            resolved_at     timestamptz,
            reopen_count    integer NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, number),
            UNIQUE (workspace_id, id),
            CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
            FOREIGN KEY (workspace_id, type_id)
                REFERENCES hd_ticket_type (workspace_id, id) ON DELETE SET NULL (type_id),
            FOREIGN KEY (workspace_id, team_id)
                REFERENCES hd_team (workspace_id, id) ON DELETE SET NULL (team_id),
            FOREIGN KEY (workspace_id, assignee_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (assignee_id),
            FOREIGN KEY (workspace_id, created_by_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by_id),
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX hd_ticket_status ON hd_ticket (workspace_id, status, priority)")
    op.execute("CREATE INDEX hd_ticket_assignee ON hd_ticket (workspace_id, assignee_id)")
    op.execute("CREATE INDEX hd_ticket_team ON hd_ticket (workspace_id, team_id)")
    op.execute("CREATE INDEX hd_ticket_contact ON hd_ticket (workspace_id, contact_id)")
    op.execute("CREATE INDEX hd_ticket_tags ON hd_ticket USING gin (tags)")

    # The conversation on a ticket. Step 3 writes `customer` (the first message)
    # and `system` lines; replies and notes come in step 4.
    op.execute(
        """
        CREATE TABLE hd_message (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id      uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id         uuid NOT NULL,
            kind              text NOT NULL
                              CHECK (kind IN ('customer', 'agent', 'ai', 'note', 'system')),
            author_member_id  uuid,
            author_name       text,
            body              text NOT NULL CHECK (length(body) BETWEEN 1 AND 50000),
            -- clock_timestamp, not now(): lines written in one transaction keep their order.
            created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, author_member_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (author_member_id)
        )
        """
    )
    op.execute("CREATE INDEX hd_message_ticket ON hd_message (workspace_id, ticket_id, created_at)")

    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE member DROP CONSTRAINT member_workspace_id_id")
