"""Push notifications to the mobile app (md P8).

- `hd_push_device`: a phone that gets banners, by its Firebase Cloud Messaging token (FCM
  delivers to Android and, through the app's Apple key in Firebase, to iPhones). One row
  per token and workspace. `session_id` is the sign-in that registered it: when that
  session ends (sign-out, password change or reset, deactivation) the device goes too
  (deleted then, and by the cascade when the session row is pruned).
- `hd_notification.push_handled_at`: when the worker finished with a notification for
  push (sent, given up, or nothing to send). Rows from before this migration count as
  handled, so turning push on never sends old banners. `push_claimed_until`: a worker is
  sending it (or waits to retry it) until then; `push_attempts`: failed tries so far.
- `hd_notification.kind` gains `handover`: a Cevanos chat waiting for Accept.

Revision ID: 0051
Revises: 0050
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

# 0046's list; `handover` is new here.
KINDS = (
    "'mention', 'assigned', 'customer_reply', 'sla_breach', 'approval', 'task', "
    "'task_overdue', 'child_resolved', 'side_reply'"
)


def _kinds(allowed: str) -> None:
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({allowed}))"
    )


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
            session_id    uuid NOT NULL REFERENCES auth_session (id) ON DELETE CASCADE,
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
    op.execute("CREATE INDEX hd_push_device_session ON hd_push_device (session_id)")
    for statement in tenant_rls_sql("hd_push_device"):
        op.execute(statement)

    op.execute(
        "ALTER TABLE hd_notification ADD COLUMN push_handled_at timestamptz, "
        "ADD COLUMN push_claimed_until timestamptz, "
        "ADD COLUMN push_attempts integer NOT NULL DEFAULT 0"
    )
    # Forced RLS: the owner sees no rows without a workspace, so lift it for the update.
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE hd_notification SET push_handled_at = created_at")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE INDEX hd_notification_push ON hd_notification (workspace_id, created_at) "
        "WHERE push_handled_at IS NULL"
    )
    _kinds(f"{KINDS}, 'handover'")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_notification WHERE kind = 'handover'")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    _kinds(KINDS)
    op.execute("DROP INDEX hd_notification_push")
    op.execute(
        "ALTER TABLE hd_notification DROP COLUMN push_handled_at, "
        "DROP COLUMN push_claimed_until, DROP COLUMN push_attempts"
    )
    op.execute("DROP TABLE hd_push_device")
