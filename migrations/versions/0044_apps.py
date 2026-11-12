"""Marketplace apps (md PL7): Shopify and Stripe for the ticket sidebar (C12).

- `hd_app_install`: one per installed app and workspace. `settings` holds what isn't
  secret (the Shopify shop name); the credential is sealed (AES-GCM, `cedesk.crypto`,
  bound to the workspace and the app) and only its last 4 characters are kept to show.
  Uninstalling deletes the row, credential and all. Nothing read from the provider is
  stored.

Revision ID: 0044
Revises: 0043
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_app_install (
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            app            text NOT NULL CHECK (app IN ('shopify', 'stripe')),
            settings       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(settings) = 'object'),
            secret_sealed  bytea NOT NULL,
            secret_hint    text NOT NULL CHECK (length(secret_hint) <= 10),
            installed_by   uuid,
            installed_at   timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, app),
            FOREIGN KEY (workspace_id, installed_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (installed_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_app_install"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_app_install")
