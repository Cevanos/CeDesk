"""Ticket tools (md X1-X4, X6, X8, X9): links, approvals, tasks and the time log.

Merge (X1) and child tickets (X3, X4) use the columns 0009 already added to `hd_ticket`
(`merged_into_id`, `parent_id`), so they need no table.

- `hd_link`: two related tickets (X2). Stored once per pair, the smaller id first, so
  "A linked to B" and "B linked to A" are the same row.
- `hd_approval` (X6): who asked whom to approve what, and the decision. The approver is
  never the requester.
- `hd_task` (X8): a to-do on a ticket with an owner and an optional due time.
  `alerted_due` is the due time its owner was told about (once per due time), as
  `hd_ticket.sla_alerted_due` does for SLA breaches.
- `hd_time_log` (X9): minutes worked on a ticket, with a note and a billable flag.
- `hd_notification.kind` gains `approval` (asked to approve, or a decision on your
  request), `task` (a task was given to you), `task_overdue` (your task passed its due
  time) and `child_resolved` (a child of your ticket was resolved).

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'mention', 'assigned', 'customer_reply', 'sla_breach'"
NEW_KINDS = "'approval', 'task', 'task_overdue', 'child_resolved'"
TABLES = ("hd_link", "hd_approval", "hd_task", "hd_time_log")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_link (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_a      uuid NOT NULL,
            ticket_b      uuid NOT NULL,
            kind          text NOT NULL DEFAULT 'linked' CHECK (kind IN ('linked')),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (ticket_a < ticket_b),
            UNIQUE (workspace_id, ticket_a, ticket_b, kind),
            FOREIGN KEY (workspace_id, ticket_a)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, ticket_b)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_link_b ON hd_link (workspace_id, ticket_b)")
    op.execute(
        """
        CREATE TABLE hd_approval (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id      uuid NOT NULL,
            requester_id   uuid NOT NULL,
            approver_id    uuid NOT NULL,
            what           text NOT NULL CHECK (length(what) BETWEEN 1 AND 500),
            amount         numeric(14, 2) CHECK (amount > 0),
            currency       text CHECK (currency ~ '^[A-Z]{3}$'),
            state          text NOT NULL DEFAULT 'pending'
                               CHECK (state IN ('pending', 'approved', 'rejected')),
            decision_note  text CHECK (length(decision_note) <= 500),
            decided_by     uuid,
            decided_at     timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (approver_id <> requester_id),
            CHECK ((state = 'pending') = (decided_at IS NULL)),
            CHECK (amount IS NULL OR currency IS NOT NULL),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, requester_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, approver_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, decided_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (decided_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_approval_ticket ON hd_approval (workspace_id, ticket_id)")
    op.execute(
        "CREATE INDEX hd_approval_waiting ON hd_approval (workspace_id, approver_id) "
        "WHERE state = 'pending'"
    )
    op.execute(
        """
        CREATE TABLE hd_task (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            owner_id      uuid NOT NULL,
            text          text NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
            due_at        timestamptz,
            alerted_due   timestamptz,
            done_at       timestamptz,
            done_by       uuid,
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, done_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (done_by),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_task_ticket ON hd_task (workspace_id, ticket_id, created_at)")
    op.execute(
        "CREATE INDEX hd_task_open ON hd_task (workspace_id, owner_id, due_at) "
        "WHERE done_at IS NULL"
    )
    op.execute("CREATE INDEX hd_task_due ON hd_task (due_at) WHERE done_at IS NULL")
    op.execute(
        """
        CREATE TABLE hd_time_log (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            member_id     uuid,
            minutes       integer NOT NULL CHECK (minutes BETWEEN 1 AND 1440),
            note          text CHECK (length(note) <= 500),
            billable      boolean NOT NULL DEFAULT false,
            logged_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (member_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_time_log_ticket ON hd_time_log (workspace_id, ticket_id, logged_at)"
    )
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({KINDS}, {NEW_KINDS}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DELETE FROM hd_notification WHERE kind IN ({NEW_KINDS})")  # noqa: S608 (constants)
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({KINDS}))"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
