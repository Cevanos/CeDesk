"""Rules can run when an SLA breaks (md W14, W15): rule event `sla_breached`.

Revision ID: 0032
Revises: 0031

The `sla_in` condition and the macros' `set_type` step need no change: conditions, actions
and steps are JSON checked by the app.

Downgrade removes the `sla_breached` rules (the old check can't hold them).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _event_check(events: str) -> None:
    op.execute("ALTER TABLE hd_rule DROP CONSTRAINT hd_rule_event_check")
    op.execute(
        f"ALTER TABLE hd_rule ADD CONSTRAINT hd_rule_event_check CHECK (event IN ({events}))"
    )


def upgrade() -> None:
    _event_check("'created', 'customer_replied', 'sla_breached'")


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the delete.
    op.execute("ALTER TABLE hd_rule NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_rule WHERE event = 'sla_breached'")
    op.execute("ALTER TABLE hd_rule FORCE ROW LEVEL SECURITY")
    _event_check("'created', 'customer_replied'")
