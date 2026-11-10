"""Forgot password (md A3): one-time reset links sent by email.

`password_reset`: only the token's SHA-256 is stored (like sessions), with a short expiry.
Used once; a successful reset removes all of the member's links.

`hd_email_out.sensitive`: the email carries a secret (a reset link, later a sign-in
code). Its text is wiped from the queue once it is sent or given up on.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE password_reset (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL,
            token_hash    bytea NOT NULL UNIQUE,
            expires_at    timestamptz NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX password_reset_member ON password_reset (member_id, created_at)")
    for statement in tenant_rls_sql("password_reset"):
        op.execute(statement)
    op.execute("ALTER TABLE hd_email_out ADD COLUMN sensitive boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_email_out DROP COLUMN sensitive")
    op.execute("DROP TABLE password_reset")
