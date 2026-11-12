"""Audit log (md S9), privacy export and erase (S11) and the sandbox (S10): Phase 3 block 3.

Audit log: who changed what setting, when, from which address.

- `hd_audit`: one row per settings change, numbered per workspace (`seq`) and chained:
  `hash` = sha256(`prev_hash` + the row's fields), so changing or removing a row breaks
  every hash after it ("Verify chain"). The app role can only add rows (no UPDATE,
  DELETE or TRUNCATE); deleting a workspace still removes its rows (the foreign key
  runs as the table owner).
- `actor_id` has no foreign key on purpose: a key with ON DELETE would rewrite a row,
  which the chain forbids. `actor_name` is kept as it was at the time.

Privacy (md S11): export or erase everything about one customer.

- `hd_privacy_request`: one row per export or erase, with who asked and, for an
  erase, how many of each thing changed (counts only, never content). An export's
  JSON file is kept in storage under `object_key` until `expires_at`.
- `hd_contact.erased_at`: set when the customer was erased; the row stays (with a
  made-up address and name) so their tickets and the reports keep their counts.
- `hd_attachment.scan_status` gains `erased`: the file was deleted from storage by an
  erase; the row stays so the ticket still shows a file was there.

Sandbox (md S10): `hd_sandbox`, at most one per workspace, holds a copy of the rules,
macros and saved replies (`config`) that an Admin edits, compares and deploys.

Revision ID: 0039
Revises: 0038
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_audit (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            seq           bigint NOT NULL CHECK (seq >= 1),
            at            timestamptz NOT NULL,
            actor_id      uuid,
            actor_name    text NOT NULL CHECK (length(actor_name) BETWEEN 1 AND 200),
            ip            text CHECK (length(ip) <= 64),
            area          text NOT NULL CHECK (area ~ '^[a-z][a-z_]{1,39}$'),
            action        text NOT NULL CHECK (action ~ '^[a-z][a-z_]{1,39}$'),
            target        text NOT NULL CHECK (length(target) <= 300),
            target_id     uuid,
            detail        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
            prev_hash     bytea NOT NULL CHECK (length(prev_hash) = 32),
            hash          bytea NOT NULL CHECK (length(hash) = 32),
            UNIQUE (workspace_id, seq)
        )
        """
    )
    op.execute("CREATE INDEX hd_audit_area ON hd_audit (workspace_id, area, seq DESC)")
    for statement in tenant_rls_sql("hd_audit"):
        op.execute(statement)
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON hd_audit FROM cedesk_app")

    op.execute("ALTER TABLE hd_contact ADD COLUMN erased_at timestamptz")
    op.execute(
        "ALTER TABLE hd_attachment DROP CONSTRAINT hd_attachment_scan_status_check, "
        "ADD CONSTRAINT hd_attachment_scan_status_check "
        "CHECK (scan_status IN ('not_scanned', 'clean', 'infected', 'scan_failed', 'erased'))"
    )
    op.execute(
        """
        CREATE TABLE hd_privacy_request (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            kind          text NOT NULL CHECK (kind IN ('export', 'erase')),
            contact_id    uuid NOT NULL,
            requested_by  uuid,
            requested_at  timestamptz NOT NULL DEFAULT now(),
            object_key    text CHECK (length(object_key) BETWEEN 1 AND 512),
            size          bigint CHECK (size >= 0),
            expires_at    timestamptz,
            counts        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(counts) = 'object'),
            FOREIGN KEY (workspace_id, contact_id) REFERENCES hd_contact (workspace_id, id),
            CHECK ((kind = 'export') = (expires_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_privacy_request_recent ON hd_privacy_request "
        "(workspace_id, requested_at DESC)"
    )
    op.execute("CREATE INDEX hd_privacy_request_contact ON hd_privacy_request (contact_id)")
    for statement in tenant_rls_sql("hd_privacy_request"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE hd_sandbox (
            workspace_id  uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            config        jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_by    uuid,
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    for statement in tenant_rls_sql("hd_sandbox"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_sandbox")
    op.execute("DROP TABLE hd_privacy_request")
    op.execute("ALTER TABLE hd_attachment NO FORCE ROW LEVEL SECURITY")  # every workspace
    op.execute("UPDATE hd_attachment SET scan_status = 'infected' WHERE scan_status = 'erased'")
    op.execute("ALTER TABLE hd_attachment FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE hd_attachment DROP CONSTRAINT hd_attachment_scan_status_check, "
        "ADD CONSTRAINT hd_attachment_scan_status_check "
        "CHECK (scan_status IN ('not_scanned', 'clean', 'infected', 'scan_failed'))"
    )
    op.execute("ALTER TABLE hd_contact DROP COLUMN erased_at")
    op.execute("DROP TABLE hd_audit")
