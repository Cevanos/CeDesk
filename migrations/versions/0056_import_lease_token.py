"""Imports: a lease token on each run, so a stalled worker can't write (fencing).

`hd_import_run.lease_token`: set to a new random value each time a worker takes the run
(`claim_due`, `claim_sync`) and cleared when it gives the run back. Every write a worker
makes about the run (saving a page and its cursor, a pause, a retry, the end of the import,
a sync look) only applies while the run still carries that worker's token. A worker that
stalled past its lease, after another took the run over, finds a different token: its
write changes nothing, its page transaction rolls back and it stops.

No data changes: runs in progress have no token until their lease next runs out and a
worker takes them again. Downgrade drops the column.

Revision ID: 0056
Revises: 0055
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_import_run ADD COLUMN lease_token uuid")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_import_run DROP COLUMN lease_token")
