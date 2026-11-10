"""Virus scan (md T18): the worker checks files with ClamAV.

- `scan_status` gains `scan_failed`: the scanner could not check the file (too big for it,
  unreadable); like `infected`, such a file is never handed out.
- `scanned_at`, `scan_result`: when, and the virus name or the scanner's error.
- An index on the files still to check.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_attachment DROP CONSTRAINT hd_attachment_scan_status_check, "
        "ADD CONSTRAINT hd_attachment_scan_status_check "
        "CHECK (scan_status IN ('not_scanned', 'clean', 'infected', 'scan_failed')), "
        "ADD COLUMN scanned_at timestamptz, "
        "ADD COLUMN scan_result text CHECK (length(scan_result) <= 200)"
    )
    op.execute(
        "CREATE INDEX hd_attachment_to_scan ON hd_attachment (workspace_id, created_at) "
        "WHERE scan_status = 'not_scanned'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_attachment_to_scan")
    op.execute("ALTER TABLE hd_attachment NO FORCE ROW LEVEL SECURITY")  # every workspace
    op.execute(
        "UPDATE hd_attachment SET scan_status = 'infected' WHERE scan_status = 'scan_failed'"
    )
    op.execute("ALTER TABLE hd_attachment FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE hd_attachment DROP CONSTRAINT hd_attachment_scan_status_check, "
        "ADD CONSTRAINT hd_attachment_scan_status_check "
        "CHECK (scan_status IN ('not_scanned', 'clean', 'infected')), "
        "DROP COLUMN scan_result, DROP COLUMN scanned_at"
    )
