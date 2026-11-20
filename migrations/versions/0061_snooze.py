"""Snooze (feature-gap plan 1.1): a Pending ticket put away until a time.

- `hd_ticket.snoozed_until`: when the worker wakes the ticket (status back to Open, the
  assignee told). Only a Pending ticket can be snoozed (CHECK). Any change of status away
  from Pending clears it (trigger `hd_ticket_snooze_clear`), whatever path made the change
  (an agent, a rule, a customer reply, an import), so a woken or reopened ticket is never
  left half-snoozed.
- Index for the wake-up pass: the snoozed tickets of a workspace, soonest first.
- `hd_notification.kind` gains `snooze_ended` (the ticket is back) and `following` (a
  ticket you follow changed; plan 1.5).

Downgrade wakes nothing: the column goes (snoozed tickets stay Pending) and the
notifications of the two new kinds are deleted.

Revision ID: 0061
Revises: 0060
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0051's list.
KINDS = (
    "'mention', 'assigned', 'customer_reply', 'sla_breach', 'approval', 'task', "
    "'task_overdue', 'child_resolved', 'side_reply', 'handover'"
)
NEW_KINDS = "'snooze_ended', 'following'"


def _kinds(allowed: str) -> None:
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({allowed}))"
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_ticket ADD COLUMN snoozed_until timestamptz, "
        "ADD CONSTRAINT hd_ticket_snooze_pending "
        "CHECK (snoozed_until IS NULL OR status = 'pending')"
    )
    op.execute(
        """
        CREATE FUNCTION hd_ticket_snooze_clear() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'pending' THEN
                NEW.snoozed_until := NULL;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER hd_ticket_snooze_clear BEFORE UPDATE OF status ON hd_ticket "
        "FOR EACH ROW WHEN (NEW.snoozed_until IS NOT NULL) "
        "EXECUTE FUNCTION hd_ticket_snooze_clear()"
    )
    op.execute(
        "CREATE INDEX hd_ticket_snoozed ON hd_ticket (workspace_id, snoozed_until) "
        "WHERE snoozed_until IS NOT NULL"
    )
    _kinds(f"{KINDS}, {NEW_KINDS}")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DELETE FROM hd_notification WHERE kind IN ({NEW_KINDS})")  # noqa: S608 (constants)
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    _kinds(KINDS)
    op.execute("DROP TRIGGER hd_ticket_snooze_clear ON hd_ticket")
    op.execute("DROP FUNCTION hd_ticket_snooze_clear()")
    op.execute("DROP INDEX hd_ticket_snoozed")
    op.execute(
        "ALTER TABLE hd_ticket DROP CONSTRAINT hd_ticket_snooze_pending, DROP COLUMN snoozed_until"
    )
