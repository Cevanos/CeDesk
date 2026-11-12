"""Live updates fix: `hd_event_log.kind` may be a single word.

0040's check asked for at least one dot (`ticket.updated`), but the stream's
`notification` event has none, so every notification row was refused and any change that
notifies an agent (assignment, mention, customer reply, task) failed. The dot is now
optional; the rest of the rule is the same.

Downgrade puts the old rule back. Events are kept only 24 hours, so it first deletes the
`notification` rows the old rule can't hold.

Revision ID: 0041
Revises: 0040
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "hd_event_log_kind_check"


def upgrade() -> None:
    op.execute(f"ALTER TABLE hd_event_log DROP CONSTRAINT {CHECK}")
    op.execute(
        f"ALTER TABLE hd_event_log ADD CONSTRAINT {CHECK} "
        r"CHECK (kind ~ '^[a-z]+(\.[a-z_]+)*$' AND length(kind) <= 60)"
    )


def downgrade() -> None:
    # The table has forced RLS; the owner sees no rows without this.
    op.execute("ALTER TABLE hd_event_log NO FORCE ROW LEVEL SECURITY")
    op.execute(r"DELETE FROM hd_event_log WHERE kind !~ '^[a-z]+(\.[a-z_]+)+$'")
    op.execute("ALTER TABLE hd_event_log FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE hd_event_log DROP CONSTRAINT {CHECK}")
    op.execute(
        f"ALTER TABLE hd_event_log ADD CONSTRAINT {CHECK} "
        r"CHECK (kind ~ '^[a-z]+(\.[a-z_]+)+$' AND length(kind) <= 60)"
    )
