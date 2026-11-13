"""Push notifications to the mobile app (md P8).

- `hd_push_device`: a phone that gets banners, by its Firebase Cloud Messaging token (FCM
  delivers to Android and, through the app's Apple key in Firebase, to iPhones). One row
  per token and workspace; signing in with the same phone as someone else moves it.
- `hd_notification.push_handled_at`: when the worker looked at a notification for push
  (sent, or nothing to send). Rows from before this migration count as handled, so turning
  push on never sends old banners.

Revision ID: 0051
Revises: 0050
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_push_device (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            member_id     uuid NOT NULL,
            platform      text NOT NULL CHECK (platform IN ('ios', 'android')),
            token         text NOT NULL CHECK (length(token) BETWEEN 20 AND 4096),
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, token),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_push_device_member ON hd_push_device (workspace_id, member_id)")
    for statement in tenant_rls_sql("hd_push_device"):
        op.execute(statement)

    op.execute("ALTER TABLE hd_notification ADD COLUMN push_handled_at timestamptz")
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the update.
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE hd_notification SET push_handled_at = created_at")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE INDEX hd_notification_push ON hd_notification (workspace_id, created_at) "
        "WHERE push_handled_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_notification_push")
    op.execute("ALTER TABLE hd_notification DROP COLUMN push_handled_at")
    op.execute("DROP TABLE hd_push_device")
