"""Two-step sign-in (md S2).

- `hd_member_2fa`: a member's authenticator secret (sealed with CEDESK_SECRET_KEY, bound to
  the member), when it was confirmed (NULL while being set up) and the last code step used.
- `hd_recovery_code`: 10 one-time codes per member, as hashes.
- `hd_security_settings.require_2fa`: every member must use it.
- `auth_session.second_factor_pending`: password checked, code not yet.

Revision ID: 0036
Revises: 0035
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_member_2fa (
            member_id      uuid PRIMARY KEY,
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            secret_sealed  bytea NOT NULL,
            confirmed_at   timestamptz,
            last_step      bigint NOT NULL DEFAULT 0,
            created_at     timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_recovery_code (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL,
            code_hash     bytea NOT NULL,
            used_at       timestamptz,
            UNIQUE (member_id, code_hash),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    for table in ("hd_member_2fa", "hd_recovery_code"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute(
        "ALTER TABLE hd_security_settings ADD COLUMN require_2fa boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE auth_session ADD COLUMN second_factor_pending boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE auth_session DROP COLUMN second_factor_pending")
    op.execute("ALTER TABLE hd_security_settings DROP COLUMN require_2fa")
    op.execute("DROP TABLE hd_recovery_code")
    op.execute("DROP TABLE hd_member_2fa")
