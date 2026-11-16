"""Governance holds as approvals (md X7) and export before retention deletes (md O2).

- `hd_approval.cevanos_hold_id`: an approval request for an action Cevanos's Governance
  holds (a refund, a cancel...). It comes with the chat's handover; nobody in CeDesk asked
  for it and anyone with the right to approve decides it, so `requester_id` and
  `approver_id` may be NULL then (and only then). One per hold.
- `hd_outbox` event `approval.decided`: the decision of a hold, sent to Cevanos by the
  worker (retried, with an Idempotency-Key).
- `hd_retention.export_before_delete`: the Admin's switch; when on, the nightly retention
  writes the tickets it deletes to a JSON file first.
- `hd_retention_export`: those files (in object storage), kept 30 days for the Admin to
  download; `contact_ids` so erasing a customer deletes the files that name them.

Downgrade refuses while any hold approval or retention export exists (it would lose them).

Revision ID: 0053
Revises: 0052
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0035's list; `approval.decided` is new here.
EVENTS_CHECK = (
    "ALTER TABLE hd_outbox ADD CONSTRAINT hd_outbox_event_check CHECK (event IN "
    "('ticket.created', 'ticket.updated', 'ticket.resolved', 'message.created', "
    "'assignment.changed', 'contact.updated', 'handover.accepted', 'security.alert'{}))"
)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE hd_approval
            ALTER COLUMN requester_id DROP NOT NULL,
            ALTER COLUMN approver_id DROP NOT NULL,
            ADD COLUMN cevanos_hold_id text
                CHECK (length(cevanos_hold_id) BETWEEN 1 AND 100),
            ADD CONSTRAINT hd_approval_who CHECK (
                cevanos_hold_id IS NOT NULL
                OR (requester_id IS NOT NULL AND approver_id IS NOT NULL))
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_approval_hold ON hd_approval (workspace_id, cevanos_hold_id) "
        "WHERE cevanos_hold_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX hd_approval_waiting_holds ON hd_approval (workspace_id) "
        "WHERE state = 'pending' AND cevanos_hold_id IS NOT NULL"
    )
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(", 'approval.decided'"))

    op.execute(
        "ALTER TABLE hd_retention ADD COLUMN export_before_delete boolean NOT NULL DEFAULT false"
    )
    op.execute(
        """
        CREATE TABLE hd_retention_export (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            object_key    text NOT NULL CHECK (length(object_key) BETWEEN 1 AND 500),
            tickets       integer NOT NULL CHECK (tickets > 0),
            size          bigint NOT NULL CHECK (size >= 0),
            contact_ids   uuid[] NOT NULL DEFAULT '{}',
            created_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL,
            UNIQUE (workspace_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_retention_export_expires ON hd_retention_export (workspace_id, expires_at)"
    )
    op.execute(
        "CREATE INDEX hd_retention_export_contacts ON hd_retention_export USING gin (contact_ids)"
    )
    for statement in tenant_rls_sql("hd_retention_export"):
        op.execute(statement)


def _count(table: str, where: str = "true") -> int:
    # Forced RLS: the owner sees no rows without a workspace, so lift it to count them.
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    found = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table} WHERE {where}"))  # noqa: S608
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return int(found.scalar_one())


def downgrade() -> None:
    holds = _count("hd_approval", "cevanos_hold_id IS NOT NULL")
    exports = _count("hd_retention_export")
    if holds or exports:
        raise RuntimeError(
            f"{holds} Governance hold approvals and {exports} retention exports exist; "
            "downgrading 0053 would lose them. Delete them on purpose first (nothing was "
            "changed)."
        )
    op.execute("DROP TABLE hd_retention_export")
    op.execute("ALTER TABLE hd_retention DROP COLUMN export_before_delete")
    op.execute("ALTER TABLE hd_outbox NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_outbox WHERE event = 'approval.decided'")
    op.execute("ALTER TABLE hd_outbox FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_outbox DROP CONSTRAINT hd_outbox_event_check")
    op.execute(EVENTS_CHECK.format(""))
    op.execute("DROP INDEX hd_approval_waiting_holds")
    op.execute("DROP INDEX hd_approval_hold")
    op.execute(
        """
        ALTER TABLE hd_approval
            DROP CONSTRAINT hd_approval_who,
            DROP COLUMN cevanos_hold_id,
            ALTER COLUMN requester_id SET NOT NULL,
            ALTER COLUMN approver_id SET NOT NULL
        """
    )
