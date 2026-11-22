"""Dashboards (feature-gap plan 3.3): a saved set of report cards.

- `hd_dashboard`: the owner, a name and whether it is shared. A personal dashboard is seen
  by its owner only; a shared one by everyone who holds the reports right. Names are unique
  per owner among personal dashboards and per workspace among shared ones (ignoring case).
- `hd_dashboard_card`: one card, in order: a saved report (`report_id`) or a built-in report
  (`preset`, a key from the code), exactly one. Deleting a saved report removes its cards.
  A shared dashboard may only hold built-in and *shared* reports (the service keeps that
  true), so everyone who sees the dashboard can run every card.
- The owner's dashboards go when the member row is deleted.

Revision ID: 0076
Revises: 0075
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_dashboard (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            owner_id      uuid NOT NULL,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            shared        boolean NOT NULL DEFAULT false,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_dashboard_personal_name ON hd_dashboard "
        "(workspace_id, owner_id, lower(name)) WHERE NOT shared"
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_dashboard_shared_name ON hd_dashboard "
        "(workspace_id, lower(name)) WHERE shared"
    )
    op.execute("CREATE INDEX hd_dashboard_owner ON hd_dashboard (workspace_id, owner_id)")
    op.execute(
        """
        CREATE TABLE hd_dashboard_card (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            dashboard_id  uuid NOT NULL,
            position      integer NOT NULL CHECK (position >= 0),
            report_id     uuid,
            preset        text CHECK (length(preset) BETWEEN 1 AND 60),
            UNIQUE (workspace_id, id),
            UNIQUE (dashboard_id, position),
            CHECK ((report_id IS NULL) <> (preset IS NULL)),
            FOREIGN KEY (workspace_id, dashboard_id)
                REFERENCES hd_dashboard (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, report_id)
                REFERENCES hd_saved_report (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_dashboard_card_report ON hd_dashboard_card (workspace_id, report_id) "
        "WHERE report_id IS NOT NULL"
    )
    for table in ("hd_dashboard", "hd_dashboard_card"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_dashboard_card")
    op.execute("DROP TABLE hd_dashboard")
