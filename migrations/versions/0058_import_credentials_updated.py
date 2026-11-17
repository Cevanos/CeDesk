"""Imports: a `credentials_updated` event (new credentials for a connection).

An Admin whose import stopped because the source refused its key (a 401/403: the key was
revoked, a token expired) can give the connection new credentials for the same account,
then resume the run (`POST /imports/connections/{id}/credentials`). The history records it.
Only the list of event kinds changes.

Downgrade deletes those events (the audit log keeps its own entry) and restores the list.

Revision ID: 0058
Revises: 0057
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'connected', 'started', 'finished', 'failed', 'sync_stopped', 'credentials_deleted', 'resumed'"
)
CHECK = "ALTER TABLE hd_import_event ADD CONSTRAINT hd_import_event_kind_check CHECK (kind IN ({}))"


def upgrade() -> None:
    op.execute("ALTER TABLE hd_import_event DROP CONSTRAINT hd_import_event_kind_check")
    op.execute(CHECK.format(KINDS + ", 'credentials_updated'"))


def downgrade() -> None:
    op.execute("ALTER TABLE hd_import_event NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_import_event WHERE kind = 'credentials_updated'")
    op.execute("ALTER TABLE hd_import_event FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_import_event DROP CONSTRAINT hd_import_event_kind_check")
    op.execute(CHECK.format(KINDS))
