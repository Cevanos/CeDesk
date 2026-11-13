"""Side conversations in Slack (md X5, the Slack half).

- `hd_app_install.app` also takes `slack`: a Slack bot (its token and signing secret,
  sealed together) that posts in a channel and hears the replies in the thread.
- `hd_side_conversation.via`: `email` (as before) or `slack`. A Slack one keeps the
  channel and the thread's first message (`slack_channel`, `slack_ts`), unique per
  workspace: that is how a reply in the thread finds it.
- A Slack message's `hd_side_message.header_message_id` is `slack:<channel>:<ts>`, so a
  reply Slack sends twice is saved once.

Downgrade removes the Slack side conversations and the Slack app installs (the old rules
can't hold them).

Revision ID: 0048
Revises: 0047
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPS = "'shopify', 'stripe', 'jira', 'slack_alerts'"


def _apps(allowed: str) -> None:
    op.execute("ALTER TABLE hd_app_install DROP CONSTRAINT IF EXISTS hd_app_install_app_check")
    op.execute(
        f"ALTER TABLE hd_app_install ADD CONSTRAINT hd_app_install_app_check "
        f"CHECK (app IN ({allowed}))"
    )


def upgrade() -> None:
    _apps(f"{APPS}, 'slack'")
    op.execute(
        """
        ALTER TABLE hd_side_conversation
            ADD COLUMN via text NOT NULL DEFAULT 'email' CHECK (via IN ('email', 'slack')),
            ADD COLUMN slack_channel text CHECK (slack_channel ~ '^[A-Z0-9]{1,30}$'),
            ADD COLUMN slack_ts text CHECK (slack_ts ~ '^[0-9]{1,20}\\.[0-9]{1,10}$'),
            ADD CONSTRAINT hd_side_conversation_slack
                CHECK ((via = 'slack') = (slack_channel IS NOT NULL AND slack_ts IS NOT NULL))
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_side_conversation_thread "
        "ON hd_side_conversation (workspace_id, slack_channel, slack_ts) WHERE via = 'slack'"
    )


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the deletes.
    op.execute("ALTER TABLE hd_side_conversation NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_side_conversation WHERE via = 'slack'")
    op.execute("ALTER TABLE hd_side_conversation FORCE ROW LEVEL SECURITY")
    op.execute("DROP INDEX hd_side_conversation_thread")
    op.execute(
        """
        ALTER TABLE hd_side_conversation
            DROP CONSTRAINT hd_side_conversation_slack,
            DROP COLUMN slack_ts,
            DROP COLUMN slack_channel,
            DROP COLUMN via
        """
    )
    op.execute("ALTER TABLE hd_app_install NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_app_install WHERE app = 'slack'")
    op.execute("ALTER TABLE hd_app_install FORCE ROW LEVEL SECURITY")
    _apps(APPS)
