"""Imports: a `resumed` event in the import history (resumable runs).

An Admin can resume a failed import from its last saved page (`POST /imports/runs/{id}/
resume`); the history records it as a `resumed` event, with who did it and where the run
carries on from. Only the list of event kinds changes; no column, no data.

Downgrade deletes the `resumed` events (the audit log keeps its own entry for each) and puts
the old list back.

Revision ID: 0055
Revises: 0054
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'connected', 'started', 'finished', 'failed', 'sync_stopped', 'credentials_deleted'"
CHECK = "ALTER TABLE hd_import_event ADD CONSTRAINT hd_import_event_kind_check CHECK (kind IN ({}))"


def upgrade() -> None:
    op.execute("ALTER TABLE hd_import_event DROP CONSTRAINT hd_import_event_kind_check")
    op.execute(CHECK.format(KINDS + ", 'resumed'"))


def downgrade() -> None:
    op.execute("ALTER TABLE hd_import_event NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_import_event WHERE kind = 'resumed'")
    op.execute("ALTER TABLE hd_import_event FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_import_event DROP CONSTRAINT hd_import_event_kind_check")
    op.execute(CHECK.format(KINDS))
