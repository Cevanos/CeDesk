"""API keys for other systems (md PL1, PL2, C3).

- `hd_api_key`: one per key an Admin made. Only the SHA-256 of the key and its last 4
  characters are kept; the key itself is shown once. `access` is read, read_write or
  admin. A key acts as `created_by`, so it goes with that member (CASCADE; members are
  deactivated, not deleted, and a key stops working with them). Revoked keys stay for the
  list and the audit log.

Revision ID: 0042
Revises: 0041
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_api_key (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            access        text NOT NULL CHECK (access IN ('read', 'read_write', 'admin')),
            key_hash      bytea NOT NULL UNIQUE CHECK (length(key_hash) = 32),
            last4         text NOT NULL CHECK (length(last4) = 4),
            created_by    uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_used_at  timestamptz,
            revoked_at    timestamptz,
            revoked_by    uuid,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, revoked_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (revoked_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_api_key_list ON hd_api_key (workspace_id, created_at DESC)")
    for statement in tenant_rls_sql("hd_api_key"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_api_key")
