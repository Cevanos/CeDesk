"""Automation safeguards and the run log (feature-gap plan 5.1, 5.5).

- `hd_rule` gains a run limit per hour and per day (counted from the run log; defaults
  1000 and 10000) and the `snooze_ended` event (a snoozed ticket's time came).
- `hd_automation_run`: one row per rule or flow run on a ticket: what started it, the
  steps it took (short descriptions only: never a message's text, an email body or a
  webhook's URL or secret), its state and result, and its loop depth. A flow run that
  waits (plan 5.3) stays here as `waiting` with the node it waits on and when it is due;
  the worker resumes it. Finished runs are kept 30 days. A run goes with its ticket.

Downgrade deletes the run log and the `snooze_ended` rules (the code below 0069 does not
know that event); the new condition and action kinds stay in the rules' JSON (older
code lists such a rule with a problem rather than running it).

Revision ID: 0069
Revises: 0068
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENTS = "'created', 'customer_replied', 'sla_breached'"


def _event_check(events: str) -> None:
    op.execute("ALTER TABLE hd_rule DROP CONSTRAINT hd_rule_event_check")
    op.execute(
        f"ALTER TABLE hd_rule ADD CONSTRAINT hd_rule_event_check CHECK (event IN ({events}))"
    )


def upgrade() -> None:
    _event_check(f"{OLD_EVENTS}, 'snooze_ended'")
    op.execute(
        "ALTER TABLE hd_rule "
        "ADD COLUMN max_runs_per_hour integer NOT NULL DEFAULT 1000 "
        "CHECK (max_runs_per_hour BETWEEN 1 AND 100000), "
        "ADD COLUMN max_runs_per_day integer NOT NULL DEFAULT 10000 "
        "CHECK (max_runs_per_day BETWEEN 1 AND 1000000)"
    )
    op.execute(
        """
        CREATE TABLE hd_automation_run (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            rule_id       uuid,
            flow_id       uuid,
            flow_version  integer,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            ticket_id     uuid NOT NULL,
            trigger       text NOT NULL CHECK (length(trigger) <= 40),
            state         text NOT NULL CHECK (state IN
                              ('done', 'waiting', 'cancelled', 'blocked', 'failed')),
            result        text NOT NULL DEFAULT '' CHECK (length(result) <= 300),
            steps         jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(steps) = 'array'),
            error         text CHECK (length(error) <= 300),
            depth         integer NOT NULL DEFAULT 1 CHECK (depth >= 1),
            outward       boolean NOT NULL DEFAULT false,
            node_id       text CHECK (length(node_id) <= 40),
            waits         integer NOT NULL DEFAULT 0,
            until_reply   boolean NOT NULL DEFAULT false,
            replied       boolean NOT NULL DEFAULT false,
            resume_at     timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            finished_at   timestamptz,
            UNIQUE (workspace_id, id),
            CHECK (state <> 'waiting' OR (resume_at IS NOT NULL AND node_id IS NOT NULL)),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, rule_id)
                REFERENCES hd_rule (workspace_id, id) ON DELETE SET NULL (rule_id)
        )
        """
    )
    # The run log page (newest first), the limits (per rule or flow, recent), loop depth
    # (per ticket, recent) and the worker's due waits.
    for name, columns in (
        ("recent", "(workspace_id, created_at DESC, id DESC)"),
        ("rule", "(rule_id, created_at) WHERE rule_id IS NOT NULL"),
        ("flow", "(flow_id, created_at) WHERE flow_id IS NOT NULL"),
        ("ticket", "(ticket_id, created_at)"),
        ("due", "(workspace_id, resume_at) WHERE state = 'waiting'"),
    ):
        op.execute(f"CREATE INDEX hd_automation_run_{name} ON hd_automation_run {columns}")
    for statement in tenant_rls_sql("hd_automation_run"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_automation_run")
    op.execute("ALTER TABLE hd_rule DROP COLUMN max_runs_per_hour, DROP COLUMN max_runs_per_day")
    op.execute("ALTER TABLE hd_rule NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_rule WHERE event = 'snooze_ended'")
    op.execute("ALTER TABLE hd_rule FORCE ROW LEVEL SECURITY")
    _event_check(OLD_EVENTS)
