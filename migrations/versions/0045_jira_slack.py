"""Jira and Slack alerts (md X10, W20, PL7).

- `hd_app_install.app` also takes `jira` and `slack_alerts`. Downgrade removes those
  installs (the old rule can't hold them), as 0032's downgrade removes the rules its old
  check can't hold.
- `hd_jira_issue`: the Jira issues on a ticket (created from CeDesk, or linked), one row
  per (ticket, issue key). The issue itself lives in Jira.

Revision ID: 0045
Revises: 0044
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _apps(allowed: str) -> None:
    op.execute("ALTER TABLE hd_app_install DROP CONSTRAINT IF EXISTS hd_app_install_app_check")
    op.execute(
        f"ALTER TABLE hd_app_install ADD CONSTRAINT hd_app_install_app_check "
        f"CHECK (app IN ({allowed}))"
    )


def upgrade() -> None:
    _apps("'shopify', 'stripe', 'jira', 'slack_alerts'")
    op.execute(
        """
        CREATE TABLE hd_jira_issue (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            issue_key     text NOT NULL
                          CHECK (issue_key ~ '^[A-Z][A-Z0-9_]{0,19}-[1-9][0-9]{0,8}$'),
            url           text NOT NULL CHECK (url LIKE 'https://%' AND length(url) <= 300),
            made_here     boolean NOT NULL,
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, ticket_id, issue_key),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_jira_issue"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_jira_issue")
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the delete.
    op.execute("ALTER TABLE hd_app_install NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_app_install WHERE app NOT IN ('shopify', 'stripe')")
    op.execute("ALTER TABLE hd_app_install FORCE ROW LEVEL SECURITY")
    _apps("'shopify', 'stripe'")
