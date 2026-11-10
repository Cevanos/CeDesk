"""Automation rules (md W15, W16) and the "customer replied" notification.

`conditions` and `actions` hold the JSON described in `domain/rules.py`; the service checks
it before saving. `event` is `created` or `customer_replied` (md 4.2 said created/updated:
rules run on customer replies, never on agent edits, so a rule can't undo an agent's choice).

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_rule (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            enabled       boolean NOT NULL DEFAULT true,
            position      integer NOT NULL DEFAULT 0,
            event         text NOT NULL CHECK (event IN ('created', 'customer_replied')),
            conditions    jsonb NOT NULL CHECK (jsonb_typeof(conditions) = 'array'),
            actions       jsonb NOT NULL CHECK (jsonb_typeof(actions) = 'array'),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_rule_order ON hd_rule (workspace_id, event, position)")
    for statement in tenant_rls_sql("hd_rule"):
        op.execute(statement)

    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        "CHECK (kind IN ('mention', 'assigned', 'customer_reply'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_notification WHERE kind = 'customer_reply'")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        "CHECK (kind IN ('mention', 'assigned'))"
    )
    op.execute("DROP TABLE hd_rule")
