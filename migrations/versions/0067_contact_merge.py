"""Contact merge (feature-gap plan 2.4).

- `hd_contact_alias`: another email address of a contact, kept when a duplicate is merged
  into it. A new email, ticket, portal sign-in or import from that address finds the
  contact it was merged into (`tickets.upsert_contact`). Unique per workspace, and never
  the same as a contact's own address (the service checks both ways). Goes with the
  contact; erasing the customer (md S11) deletes them.
- `hd_contact_merge`: the history of merges into a contact, for its timeline: who merged,
  when, the duplicate's old id, name and address (erased with the customer) and what
  moved (counts only). The duplicate's row itself is deleted by the merge.

Revision ID: 0067
Revises: 0066
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_contact_alias (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            email         text NOT NULL
                          CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            contact_id    uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, email),
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_contact_alias_contact ON hd_contact_alias (workspace_id, contact_id)"
    )
    op.execute(
        """
        CREATE TABLE hd_contact_merge (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            contact_id      uuid NOT NULL,
            merged_id       uuid NOT NULL,
            merged_name     text,
            merged_email    text,
            merged_by       uuid,
            moved           jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(moved) = 'object'),
            created_at      timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, merged_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (merged_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_contact_merge_contact ON hd_contact_merge "
        "(workspace_id, contact_id, created_at DESC)"
    )
    for table in ("hd_contact_alias", "hd_contact_merge"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_contact_merge")
    op.execute("DROP TABLE hd_contact_alias")
