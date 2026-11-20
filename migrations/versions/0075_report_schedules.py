"""Scheduled report emails (feature-gap plan 3.5): a report sent to team members on a
schedule.

- `hd_report_schedule`: who made it (`owner_id`), what to send (a saved report, or a built-in
  one, exactly one) and over which preset range, how often (daily, weekly on a weekday,
  monthly on a day 1-28) and at which hour of the workspace's own day, when it is next due
  (`next_run_at`) and when it last went out. The worker claims a due row with `FOR UPDATE SKIP
  LOCKED` and moves `next_run_at` on in the same transaction that queues the emails, so a
  restart or two workers never send twice, and after an outage it sends once, not once per
  missed period.
- `hd_report_schedule_recipient`: the members it goes to. Always members, never free
  addresses; the worker checks at every send that each still holds the reports right with a
  scope as wide as the owner's.
- A schedule goes when its owner or its saved report goes; a recipient row goes with the
  member.

Revision ID: 0075
Revises: 0074
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_report_schedule (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            owner_id      uuid NOT NULL,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            report_id     uuid,
            preset        text CHECK (length(preset) BETWEEN 1 AND 60),
            range_preset  text NOT NULL CHECK (length(range_preset) BETWEEN 1 AND 20),
            cadence       text NOT NULL CHECK (cadence IN ('daily', 'weekly', 'monthly')),
            weekday       smallint CHECK (weekday BETWEEN 1 AND 7),
            day_of_month  smallint CHECK (day_of_month BETWEEN 1 AND 28),
            hour          smallint NOT NULL CHECK (hour BETWEEN 0 AND 23),
            enabled       boolean NOT NULL DEFAULT true,
            next_run_at   timestamptz NOT NULL,
            last_sent_at  timestamptz,
            last_error    text CHECK (length(last_error) <= 300),
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            CHECK ((report_id IS NULL) <> (preset IS NULL)),
            CHECK ((cadence = 'weekly') = (weekday IS NOT NULL)),
            CHECK ((cadence = 'monthly') = (day_of_month IS NOT NULL)),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, report_id)
                REFERENCES hd_saved_report (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    # What the worker's minute pass reads: the schedules that are due.
    op.execute(
        "CREATE INDEX hd_report_schedule_due ON hd_report_schedule (workspace_id, next_run_at) "
        "WHERE enabled"
    )
    op.execute(
        "CREATE INDEX hd_report_schedule_owner ON hd_report_schedule (workspace_id, owner_id)"
    )
    op.execute(
        """
        CREATE TABLE hd_report_schedule_recipient (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            schedule_id   uuid NOT NULL,
            member_id     uuid NOT NULL,
            PRIMARY KEY (schedule_id, member_id),
            FOREIGN KEY (workspace_id, schedule_id)
                REFERENCES hd_report_schedule (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_report_schedule_recipient_member "
        "ON hd_report_schedule_recipient (workspace_id, member_id)"
    )
    for table in ("hd_report_schedule", "hd_report_schedule_recipient"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_report_schedule_recipient")
    op.execute("DROP TABLE hd_report_schedule")
