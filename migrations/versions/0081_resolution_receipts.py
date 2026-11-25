"""Resolution receipts (differentiator plan #5): what was done on a ticket and whether
the result was checked.

- `hd_resolution`: one receipt per ticket (a reopen and a new resolve replace it; the old
  one is kept in the audit log). The request, the action taken and the result in a few
  words; how it was verified (`unverified`, `agent`, `shopify`, `stripe`, the last two read
  from the connected app, never written to it); whether the customer was told; an
  optional pending item. Covered by the privacy export and erase (erase blanks the text).
- `hd_resolution_setting`: the workspace's setting, one row (no row: `off`). `required`
  can be limited to some teams.

Revision ID: 0081
Revises: 0080
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_resolution (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id          uuid NOT NULL,
            request            text CHECK (length(request) <= 300),
            action_taken       text NOT NULL CHECK (length(action_taken) <= 500),
            result             text CHECK (length(result) <= 300),
            verified           text NOT NULL DEFAULT 'unverified'
                               CHECK (verified IN ('unverified', 'agent', 'shopify', 'stripe')),
            verified_at        timestamptz,
            customer_notified  boolean NOT NULL DEFAULT false,
            pending_item       text CHECK (length(pending_item) <= 200),
            resolved_by        uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, ticket_id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, resolved_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (resolved_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_resolution_created ON hd_resolution (workspace_id, created_at)")
    op.execute(
        """
        CREATE TABLE hd_resolution_setting (
            workspace_id    uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            mode            text NOT NULL DEFAULT 'off'
                            CHECK (mode IN ('off', 'optional', 'required')),
            required_teams  uuid[] NOT NULL DEFAULT '{}'
                            CHECK (cardinality(required_teams) <= 200),
            updated_by      uuid,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for table in ("hd_resolution", "hd_resolution_setting"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_resolution_setting")
    op.execute("DROP TABLE hd_resolution")
