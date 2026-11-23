"""Email campaigns (feature-gap plan 6.1, 6.2, 6.4).

- `hd_campaign`: one email to the customers of a segment. Name, segment, subject, body
  (text with the reply placeholders, a little Markdown), the support address it comes
  from, open tracking on or off, and its state: draft, scheduled, sending, paused, sent,
  cancelled or failed. When it starts, the segment's name, the address and its display
  name are copied, so the results still say what was sent if those change later.
- `hd_campaign_recipient`: the recipients, copied from the segment when the campaign
  starts (a later change to the segment changes nothing). One row per address, which is
  the send queue too: the worker claims rows (FOR UPDATE SKIP LOCKED) and marks each one
  `sending` before it hands the email to the mail server, so a row is sent at most once
  (a worker that stops mid-send leaves it `failed`, never sent twice). The links in the
  email carry random tokens whose SHA-256 hashes are kept here (unsubscribe, open pixel),
  never the address. Results per row: sent, failed, bounced, skipped (suppressed or
  erased since it was queued), cancelled; first open and open count, unsubscribe and
  spam-report times. Erasing a customer clears their address and name here.

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_campaign (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name             text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            segment_id       uuid,
            segment_name     text,
            subject          text NOT NULL DEFAULT '' CHECK (length(subject) <= 200),
            body             text NOT NULL DEFAULT '' CHECK (length(body) <= 50000),
            from_address_id  uuid,
            from_address     text,
            from_name        text,
            track_opens      boolean NOT NULL DEFAULT false,
            state            text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft',
                                 'scheduled', 'sending', 'paused', 'sent', 'cancelled',
                                 'failed')),
            scheduled_at     timestamptz,
            started_at       timestamptz,
            finished_at      timestamptz,
            recipient_count  integer NOT NULL DEFAULT 0,
            last_error       text CHECK (length(last_error) <= 300),
            created_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            CHECK (state <> 'scheduled' OR scheduled_at IS NOT NULL),
            FOREIGN KEY (workspace_id, segment_id)
                REFERENCES hd_segment (workspace_id, id) ON DELETE SET NULL (segment_id),
            FOREIGN KEY (workspace_id, from_address_id)
                REFERENCES hd_email_address (workspace_id, id)
                ON DELETE SET NULL (from_address_id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_campaign_recent ON hd_campaign (workspace_id, created_at DESC, id)")
    op.execute(
        "CREATE INDEX hd_campaign_due ON hd_campaign (workspace_id, scheduled_at) "
        "WHERE state IN ('scheduled', 'sending')"
    )
    for statement in tenant_rls_sql("hd_campaign"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE hd_campaign_recipient (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            campaign_id        uuid NOT NULL,
            contact_id         uuid,
            email              text CHECK (length(email) BETWEEN 3 AND 254),
            name               text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
            status             text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued',
                                   'sending', 'sent', 'failed', 'bounced', 'skipped',
                                   'cancelled')),
            attempts           integer NOT NULL DEFAULT 0,
            next_attempt_at    timestamptz NOT NULL DEFAULT now(),
            claimed_until      timestamptz,
            error              text CHECK (length(error) <= 300),
            header_message_id  text CHECK (length(header_message_id) <= 300),
            unsubscribe_hash   bytea UNIQUE,
            open_hash          bytea UNIQUE,
            created_at         timestamptz NOT NULL DEFAULT now(),
            sent_at            timestamptz,
            failed_at          timestamptz,
            bounced_at         timestamptz,
            first_opened_at    timestamptz,
            open_count         integer NOT NULL DEFAULT 0,
            unsubscribed_at    timestamptz,
            complained_at      timestamptz,
            UNIQUE (workspace_id, id),
            UNIQUE (campaign_id, contact_id),
            FOREIGN KEY (workspace_id, campaign_id)
                REFERENCES hd_campaign (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE SET NULL (contact_id)
        )
        """
    )
    # One email per address per campaign; the queue; results by status; bounce matching
    # by Message-ID; a customer's rows (privacy); the per-minute and per-day counts.
    r = "hd_campaign_recipient"
    for statement in (
        f"CREATE UNIQUE INDEX {r}_address ON {r} (campaign_id, email) WHERE email IS NOT NULL",
        f"CREATE INDEX {r}_queue ON {r} (campaign_id, next_attempt_at) WHERE status = 'queued'",
        f"CREATE INDEX {r}_status ON {r} (campaign_id, status, id)",
        f"CREATE INDEX {r}_message ON {r} (workspace_id, header_message_id) "
        "WHERE header_message_id IS NOT NULL",
        f"CREATE INDEX {r}_contact ON {r} (contact_id) WHERE contact_id IS NOT NULL",
        f"CREATE INDEX {r}_sent ON {r} (workspace_id, sent_at) WHERE sent_at IS NOT NULL",
    ):
        op.execute(statement)
    for statement in tenant_rls_sql("hd_campaign_recipient"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_campaign_recipient")
    op.execute("DROP TABLE hd_campaign")
