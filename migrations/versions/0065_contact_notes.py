"""Contact list and notes (feature-gap plan 2.1, 2.2).

- `hd_contact_note`: a private note about a customer (never shown to them): who wrote it,
  when, and the words. Goes with the contact; the author's name stays readable after the
  member is removed (`author_id` becomes null, the list shows "Former member").
  Erasing the customer (md S11) deletes their notes.
- An index on the contact's name (lower case), for the contact list's A-Z order.

Revision ID: 0065
Revises: 0064
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_contact_note (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            contact_id    uuid NOT NULL,
            author_id     uuid,
            body          text NOT NULL CHECK (length(body) BETWEEN 1 AND 5000),
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, author_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (author_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_contact_note_contact ON hd_contact_note "
        "(workspace_id, contact_id, created_at DESC)"
    )
    for statement in tenant_rls_sql("hd_contact_note"):
        op.execute(statement)
    op.execute("CREATE INDEX hd_contact_name ON hd_contact (workspace_id, lower(name), id)")


def downgrade() -> None:
    op.execute("DROP INDEX hd_contact_name")
    op.execute("DROP TABLE hd_contact_note")
