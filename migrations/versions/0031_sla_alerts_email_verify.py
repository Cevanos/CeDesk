"""SLA breach alerts (md W14) and verifying support email addresses (C10).

- `hd_ticket.sla_alerted_due`: the due time (`sla_due_at`) the breach alert was sent
  for. A ticket is alerted when `sla_due_at` has passed and differs from it, so each
  clock (first reply, then resolution) alerts once, and a due time moved later (a new
  priority) can alert again. Tickets already overdue when this migration runs count as
  alerted: nobody gets a flood of old breaches on the first minute.
- `hd_notification.kind` gains `sla_breach`.

Support email addresses (C10), on `hd_email_address`:
- `forwarding_verified_at`: a forwarding-check email came back in through CeDesk.
- `verify_code_hash` / `verify_sent_at`: the last check's one-time code (only its hash).
- `verify_email_id`: the queued check email (`hd_email_out`), so the page can show
  whether it was sent or failed.
- `dkim_selector`: where the Admin's mail service keeps the DKIM key.
- `dns_checked_at` / `dns_result`: the last SPF, DKIM and DMARC check, as shown.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'mention', 'assigned', 'customer_reply'"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE hd_email_address
            ADD COLUMN forwarding_verified_at timestamptz,
            ADD COLUMN verify_code_hash text CHECK (length(verify_code_hash) = 64),
            ADD COLUMN verify_sent_at timestamptz,
            ADD COLUMN verify_email_id uuid,
            ADD COLUMN dkim_selector text CHECK (dkim_selector ~ '^[a-z0-9][a-z0-9._-]{0,62}$'),
            ADD COLUMN dns_checked_at timestamptz,
            ADD COLUMN dns_result jsonb
        """
    )
    op.execute("ALTER TABLE hd_ticket ADD COLUMN sla_alerted_due timestamptz")
    op.execute("ALTER TABLE hd_ticket NO FORCE ROW LEVEL SECURITY")  # every workspace
    op.execute(
        "UPDATE hd_ticket SET sla_alerted_due = sla_due_at "
        "WHERE sla_due_at <= now() AND status <> 'resolved'"
    )
    op.execute("ALTER TABLE hd_ticket FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE INDEX hd_ticket_sla_alert ON hd_ticket (workspace_id, sla_due_at) "
        "WHERE status <> 'resolved'"
    )
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({KINDS}, 'sla_breach'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_notification NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_notification WHERE kind = 'sla_breach'")
    op.execute("ALTER TABLE hd_notification FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_notification DROP CONSTRAINT hd_notification_kind_check")
    op.execute(
        "ALTER TABLE hd_notification ADD CONSTRAINT hd_notification_kind_check "
        f"CHECK (kind IN ({KINDS}))"
    )
    op.execute("DROP INDEX hd_ticket_sla_alert")
    op.execute("ALTER TABLE hd_ticket DROP COLUMN sla_alerted_due")
    op.execute(
        "ALTER TABLE hd_email_address DROP COLUMN forwarding_verified_at, "
        "DROP COLUMN verify_code_hash, DROP COLUMN verify_sent_at, DROP COLUMN verify_email_id, "
        "DROP COLUMN dkim_selector, "
        "DROP COLUMN dns_checked_at, DROP COLUMN dns_result"
    )
