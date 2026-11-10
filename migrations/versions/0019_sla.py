"""SLA (md W9-W13, 6.1) wired to tickets.

Settings tables (an empty table means "the defaults in domain/sla.py": the mock's targets,
its three policies, Mon-Sat 9-19 in Asia/Kolkata, no holidays):
- `hd_sla_target`: per priority, first-reply and resolve minutes (W9).
- `hd_sla_policy`: ordered policies, first enabled match wins (W10; editing comes later).
- `hd_sla_calendar`: one row per workspace: time zone, days, hours, or 24/7 (W11).
- `hd_holiday`: days the clock does not run (W12).

`hd_ticket.sla_due_at`: when the ticket's current clock (first reply until it is
replied, then resolution) runs out; NULL once resolved. Kept by the service whenever
an input changes; only the `sla` list sort reads it (the pill is worked out on read).

`hd_outbox` also accepts `contact.updated` (a customer's name or VIP flag changed).

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EPOCH = "extract(epoch FROM ({} - TIMESTAMPTZ '1970-01-01 00:00:00+00'))"
# Must match repo.tickets._SLA_KEYS exactly, or the index is not used.
SLA_KEYS = (
    "(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END)",
    "(CASE WHEN status = 'resolved' THEN -" + EPOCH.format("resolved_at") + " "
    "ELSE " + EPOCH.format("coalesce(sla_due_at, created_at)") + " END)",
    "(CASE WHEN status = 'resolved' THEN -number ELSE number END)",
)
PRIORITIES = "('urgent', 'high', 'medium', 'low')"
MINUTES = "BETWEEN 1 AND 43200"  # up to 30 days
EVENTS_CHECK = (
    "ALTER TABLE hd_outbox ADD CONSTRAINT hd_outbox_event_check CHECK (event IN "
    "('ticket.created', 'ticket.updated', 'ticket.resolved', 'message.created', "
    "'assignment.changed'{}))"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE hd_sla_target (
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            priority         text NOT NULL CHECK (priority IN {PRIORITIES}),
            first_reply_min  integer NOT NULL CHECK (first_reply_min {MINUTES}),
            resolve_min      integer NOT NULL CHECK (resolve_min {MINUTES}),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, priority),
            CHECK (first_reply_min <= resolve_min)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE hd_sla_policy (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name             text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            position         integer NOT NULL,
            enabled          boolean NOT NULL DEFAULT true,
            vip_only         boolean NOT NULL DEFAULT false,
            channels         text[] NOT NULL DEFAULT '{{}}',
            priorities       text[] NOT NULL DEFAULT '{{}}',
            first_reply_min  integer CHECK (first_reply_min {MINUTES}),
            resolve_min      integer CHECK (resolve_min {MINUTES}),
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name),
            UNIQUE (workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_sla_calendar (
            workspace_id  uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            timezone      text NOT NULL CHECK (length(timezone) BETWEEN 1 AND 64),
            days          integer[] NOT NULL
                          CHECK (cardinality(days) BETWEEN 1 AND 7 AND days <@ '{1,2,3,4,5,6,7}'),
            opens         time NOT NULL,
            closes        time NOT NULL,
            always_open   boolean NOT NULL DEFAULT false,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (opens < closes)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_holiday (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            day           date NOT NULL,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            UNIQUE (workspace_id, day)
        )
        """
    )
    for table in ("hd_sla_target", "hd_sla_policy", "hd_sla_calendar", "hd_holiday"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)

    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(", 'contact.updated'"))
    op.execute("ALTER TABLE hd_ticket ADD COLUMN sla_due_at timestamptz")
    op.execute(
        "CREATE INDEX hd_ticket_sort_sla ON hd_ticket (workspace_id, " + ", ".join(SLA_KEYS) + ")"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_ticket_sort_sla")
    op.execute("ALTER TABLE hd_ticket DROP COLUMN sla_due_at")
    op.execute("ALTER TABLE hd_outbox NO FORCE ROW LEVEL SECURITY")  # see every workspace
    op.execute("DELETE FROM hd_outbox WHERE event = 'contact.updated'")
    op.execute("ALTER TABLE hd_outbox FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(""))
    for table in ("hd_holiday", "hd_sla_calendar", "hd_sla_policy", "hd_sla_target"):
        op.execute(f"DROP TABLE {table}")
