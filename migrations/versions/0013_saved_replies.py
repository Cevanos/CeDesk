"""Saved replies (md W19): `/refund` and friends, typed after `/` in the reply box.

`shortcut` is stored lower-case without the slash and is unique per workspace. `source`
says where a reply came from (`default`, `manual`, `calendly`, or an import source such as
`intercom`); `source_id` is the id it had there, so running an import again finds it.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_saved_reply (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            shortcut      text NOT NULL CHECK (shortcut ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
            body          text NOT NULL CHECK (length(body) BETWEEN 1 AND 10000),
            source        text NOT NULL DEFAULT 'manual' CHECK (source ~ '^[a-z0-9_-]{1,40}$'),
            source_id     text CHECK (length(source_id) BETWEEN 1 AND 200),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, shortcut),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_saved_reply_source ON hd_saved_reply "
        "(workspace_id, source, source_id) WHERE source_id IS NOT NULL"
    )
    for statement in tenant_rls_sql("hd_saved_reply"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_saved_reply")
