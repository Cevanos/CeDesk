"""Campaign compliance (feature-gap plan 6.3): the suppression list and the campaign
settings, which must exist before any bulk email.

- `hd_suppression`: addresses campaigns never go to, one row per address per workspace,
  keyed by `email_hash` = sha256("<workspace id>:<lower-case address>") (the same in SQL
  and in `domain/campaigns.py`). `email` is shown to Admins; it is NULL once the person
  was erased (privacy), and the hash alone keeps them from ever being mailed again.
  Reasons: unsubscribed (their link or one-click), hard_bounce, complaint (a spam report),
  manual (an Admin), erased. Only campaigns read it: ticket replies, transcripts and the
  other transactional emails never look at it.
- `hd_campaign_settings`: per workspace, the postal address every campaign email's footer
  carries (CAN-SPAM and similar laws ask for one). No campaign can start without it.
- `hd_email_out.body_html`: an optional HTML part, for a campaign's test email (it goes
  through the normal queue to the Admin's own address).

Revision ID: 0071
Revises: 0070
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_suppression (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            email_hash    bytea NOT NULL CHECK (length(email_hash) = 32),
            email         text CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            reason        text NOT NULL CHECK (reason IN
                              ('unsubscribed', 'hard_bounce', 'complaint', 'manual', 'erased')),
            source        text NOT NULL CHECK (source IN
                              ('link', 'one_click', 'bounce', 'complaint', 'admin', 'erase')),
            campaign_id   uuid,
            note          text CHECK (length(note) <= 200),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, email_hash),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_suppression_recent ON hd_suppression (workspace_id, created_at DESC, id)"
    )
    for statement in tenant_rls_sql("hd_suppression"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE hd_campaign_settings (
            workspace_id    uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            postal_address  text NOT NULL CHECK (length(postal_address) BETWEEN 10 AND 300),
            updated_by      uuid,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    for statement in tenant_rls_sql("hd_campaign_settings"):
        op.execute(statement)

    op.execute("ALTER TABLE hd_email_out ADD COLUMN body_html text")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_email_out DROP COLUMN body_html")
    op.execute("DROP TABLE hd_campaign_settings")
    op.execute("DROP TABLE hd_suppression")
