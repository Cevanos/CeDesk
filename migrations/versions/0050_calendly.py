"""The Calendly app (md PL7, W19).

- `hd_app_install.app` also takes `calendly`. Its only setting is the public booking link,
  so it has no credential: `secret_sealed` and `secret_hint` may be NULL, for Calendly
  only (every other app keeps one).
- Installing it adds the saved reply /book (`source = calendly`, `source_id = book`);
  uninstalling removes it. No new column: `hd_saved_reply.source` already takes any word.

Downgrade removes the Calendly installs (the old rules can't hold them); their /book
replies stay as ordinary saved replies.

Revision ID: 0050
Revises: 0049
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPS = "'shopify', 'stripe', 'jira', 'slack_alerts', 'slack'"


def _apps(allowed: str) -> None:
    op.execute("ALTER TABLE hd_app_install DROP CONSTRAINT IF EXISTS hd_app_install_app_check")
    op.execute(
        f"ALTER TABLE hd_app_install ADD CONSTRAINT hd_app_install_app_check "
        f"CHECK (app IN ({allowed}))"
    )


def upgrade() -> None:
    _apps(f"{APPS}, 'calendly'")
    op.execute(
        """
        ALTER TABLE hd_app_install
            ALTER COLUMN secret_sealed DROP NOT NULL,
            ALTER COLUMN secret_hint DROP NOT NULL,
            ADD CONSTRAINT hd_app_install_secret
                CHECK ((app = 'calendly') = (secret_sealed IS NULL AND secret_hint IS NULL))
        """
    )


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the delete.
    op.execute("ALTER TABLE hd_app_install NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_app_install WHERE app = 'calendly'")
    op.execute("ALTER TABLE hd_app_install FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        ALTER TABLE hd_app_install
            DROP CONSTRAINT hd_app_install_secret,
            ALTER COLUMN secret_sealed SET NOT NULL,
            ALTER COLUMN secret_hint SET NOT NULL
        """
    )
    _apps(APPS)
