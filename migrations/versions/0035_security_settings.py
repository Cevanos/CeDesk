"""Security settings (md S3-S6, S8).

- `hd_security_settings`, one row per workspace (none = the defaults): minimum password
  length 8-64 (default 10, as before), password expiry (never, 90 or 180 days), sign-out
  after (1 h idle, 8 h, 12 h (default, as before) or 7 days), IP allowlist (CIDR, empty =
  off), attachments need sign-in, Admin email alerts (on by default).
- `member.password_changed_at` for expiry; existing members count from now.
- `auth_session.last_used_at` for the idle sign-out.
- Outbox event `security.alert`: the worker emails the Admins.

Revision ID: 0035
Revises: 0034
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS_CHECK = (
    "ALTER TABLE hd_outbox ADD CONSTRAINT hd_outbox_event_check CHECK (event IN "
    "('ticket.created', 'ticket.updated', 'ticket.resolved', 'message.created', "
    "'assignment.changed', 'contact.updated', 'handover.accepted'{}))"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_security_settings (
            workspace_id          uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            password_min          integer NOT NULL DEFAULT 10
                                      CHECK (password_min BETWEEN 8 AND 64),
            password_expiry_days  integer CHECK (password_expiry_days IN (90, 180)),
            session_length        text NOT NULL DEFAULT '12h'
                                      CHECK (session_length IN ('1h_idle', '8h', '12h', '7d')),
            ip_allowlist          cidr[] NOT NULL DEFAULT '{}'
                                      CHECK (cardinality(ip_allowlist) <= 100),
            attachments_need_sign_in boolean NOT NULL DEFAULT false,
            admin_alerts          boolean NOT NULL DEFAULT true,
            updated_by            uuid,
            updated_at            timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_security_settings"):
        op.execute(statement)
    op.execute(
        "ALTER TABLE member ADD COLUMN password_changed_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE auth_session ADD COLUMN last_used_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(", 'security.alert'"))


def downgrade() -> None:
    op.execute("ALTER TABLE hd_outbox NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_outbox WHERE event = 'security.alert'")
    op.execute("ALTER TABLE hd_outbox FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(""))
    op.execute("ALTER TABLE auth_session DROP COLUMN last_used_at")
    op.execute("ALTER TABLE member DROP COLUMN password_changed_at")
    op.execute("DROP TABLE hd_security_settings")
