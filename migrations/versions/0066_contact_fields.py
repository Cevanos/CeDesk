"""Custom attributes on contacts (feature-gap plan 2.3).

- `hd_contact_field`: the workspace's contact fields. `key` (unique, never changes),
  `label`, `kind` (text, number, date, select, checkbox; never changes), `options` (select
  only) and `position` (the order on the customer page).
- `hd_contact.attributes`: the contact's values, an object keyed by field key (checked by
  the service against the field's kind). A GIN index serves the contact list's and
  segments' "value equals" filters (`attributes @> ...`). Deleting a field removes its
  key from every contact. Erasing the customer (md S11) empties it.

Revision ID: 0066
Revises: 0065
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_contact_field (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            key           text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,39}$'),
            label         text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
            kind          text NOT NULL
                          CHECK (kind IN ('text', 'number', 'date', 'select', 'checkbox')),
            options       jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(options) = 'array'),
            position      integer NOT NULL DEFAULT 0,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, key),
            UNIQUE (workspace_id, id)
        )
        """
    )
    for statement in tenant_rls_sql("hd_contact_field"):
        op.execute(statement)
    op.execute(
        "ALTER TABLE hd_contact ADD COLUMN attributes jsonb NOT NULL DEFAULT '{}' "
        "CHECK (jsonb_typeof(attributes) = 'object')"
    )
    op.execute(
        "CREATE INDEX hd_contact_attributes ON hd_contact USING gin (attributes jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_contact_attributes")
    op.execute("ALTER TABLE hd_contact DROP COLUMN attributes")
    op.execute("DROP TABLE hd_contact_field")
