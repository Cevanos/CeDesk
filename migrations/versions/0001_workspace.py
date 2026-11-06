"""Workspace: the tenant root.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            time_zone   text NOT NULL DEFAULT 'UTC',
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The workspace row is visible only to a session set to that workspace.
    for statement in tenant_rls_sql("workspace", column="id"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE workspace")
