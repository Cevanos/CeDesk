"""Macros (md W18): a named list of steps an agent runs on a ticket in one click.

`steps` holds the JSON described in `domain/macros.py`; the service checks it before
saving. `position` is the order in the agent's Macros menu.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_macro (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            position      integer NOT NULL DEFAULT 0,
            steps         jsonb NOT NULL CHECK (jsonb_typeof(steps) = 'array'),
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
    op.execute("CREATE INDEX hd_macro_order ON hd_macro (workspace_id, position)")
    for statement in tenant_rls_sql("hd_macro"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_macro")
