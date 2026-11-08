"""Two database hardening items from the Phase 1 audit.

1. `auth_session.member_id` becomes a composite key on (workspace_id, member_id), like
   every other reference between workspace tables: a foreign-key check ignores RLS, so
   a single-column key would accept a member id from another workspace.
2. The app role may no longer delete a workspace row. Deleting one cascades to all of
   its data; that is an owner-only operation, never something a request can do.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE auth_session DROP CONSTRAINT auth_session_member_id_fkey")
    op.execute(
        "ALTER TABLE auth_session ADD CONSTRAINT auth_session_member_id_fkey "
        "FOREIGN KEY (workspace_id, member_id) REFERENCES member (workspace_id, id) "
        "ON DELETE CASCADE"
    )
    op.execute("REVOKE DELETE, TRUNCATE ON workspace FROM cedesk_app")


def downgrade() -> None:
    op.execute("GRANT DELETE ON workspace TO cedesk_app")
    op.execute("ALTER TABLE auth_session DROP CONSTRAINT auth_session_member_id_fkey")
    op.execute(
        "ALTER TABLE auth_session ADD CONSTRAINT auth_session_member_id_fkey "
        "FOREIGN KEY (member_id) REFERENCES member (id) ON DELETE CASCADE"
    )
