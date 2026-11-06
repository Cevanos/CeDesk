"""Members (agents), sign-in sessions and the workspace slug directory.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Global: maps the slug typed at sign-in to a workspace before any workspace
    # is known. Holds no workspace data, so it has no RLS (listed in the tenancy
    # guard's GLOBAL_TABLES). The app may add rows, never change or remove them.
    op.execute(
        """
        CREATE TABLE workspace_directory (
            slug          text PRIMARY KEY CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
            workspace_id  uuid NOT NULL UNIQUE REFERENCES workspace(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON workspace_directory FROM cedesk_app")

    op.execute(
        """
        CREATE TABLE member (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            email          text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
            name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            role           text NOT NULL
                           CHECK (role IN ('admin', 'supervisor', 'agent', 'light_agent')),
            password_hash  text NOT NULL,
            is_active      boolean NOT NULL DEFAULT true,
            failed_logins  integer NOT NULL DEFAULT 0,
            locked_until   timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, email)
        )
        """
    )
    for statement in tenant_rls_sql("member"):
        op.execute(statement)

    # Only a SHA-256 of the token is stored; the token itself is shown once, at sign-in.
    op.execute(
        """
        CREATE TABLE auth_session (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL REFERENCES member(id) ON DELETE CASCADE,
            token_hash    bytea NOT NULL UNIQUE,
            created_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL,
            revoked_at    timestamptz
        )
        """
    )
    op.execute("CREATE INDEX auth_session_member ON auth_session (workspace_id, member_id)")
    for statement in tenant_rls_sql("auth_session"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE auth_session")
    op.execute("DROP TABLE member")
    op.execute("DROP TABLE workspace_directory")
