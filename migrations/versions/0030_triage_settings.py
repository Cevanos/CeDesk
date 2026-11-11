"""Triage and redaction settings (md I1, I5): one row per workspace, made when an Admin
first saves. No row means the defaults: the built-in keyword lists and card redaction on.

- `topic_keywords`: {"Refund / return": ["refund", ...], ...}; NULL means the built-in
  lists (domain/triage.py), so later improvements to them reach workspaces that never
  edited theirs.
- `redact_cards`: card numbers are removed from customer messages before saving (on by
  default). Phase 3's security settings show the same switch.

`hd_ticket.sentiment_by_cevanos`: Cevanos's AI sent the mood (`conversation.classified`);
keyword triage on later customer messages then leaves it alone.

Team report (md R1, R2): indexes for "resolved in the period" and "rated in the period".

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_triage_settings (
            workspace_id     uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            topic_keywords   jsonb CHECK (topic_keywords IS NULL
                                          OR jsonb_typeof(topic_keywords) = 'object'),
            redact_cards     boolean NOT NULL DEFAULT true,
            updated_by       uuid,
            updated_at       timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_triage_settings"):
        op.execute(statement)
    op.execute(
        "ALTER TABLE hd_ticket ADD COLUMN sentiment_by_cevanos boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "CREATE INDEX hd_ticket_resolved_at ON hd_ticket (workspace_id, resolved_at) "
        "WHERE resolved_at IS NOT NULL"
    )
    op.execute("CREATE INDEX hd_csat_rated_at ON hd_csat (workspace_id, rated_at)")


def downgrade() -> None:
    op.execute("DROP INDEX hd_csat_rated_at")
    op.execute("DROP INDEX hd_ticket_resolved_at")
    op.execute("ALTER TABLE hd_ticket DROP COLUMN sentiment_by_cevanos")
    op.execute("DROP TABLE hd_triage_settings")
