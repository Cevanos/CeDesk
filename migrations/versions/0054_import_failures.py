"""Imports: records that could not be read, and retries that end (audit P0-2).

- `hd_import_failure`: one row per record a run could not read and left out, so one deleted
  or broken ticket never fails a whole import and is never lost without a trace. `category`
  `not_found` (the source no longer has it: a 404 after listing it), `temporary` (it kept
  failing after every retry of its page) or `incomplete` (imported, but part of its history
  could not be read). `attempts` is how many tries it got; a sync look that meets it again
  adds to it. `status` `open` until a later read gets the record in full (an import run
  again, a sync look), then `resolved`; or `dismissed` by an Admin (`resolved_by`).
  `last_error` is CeDesk's own message (`SourceError`), never a credential or the
  customer's text. Unique per run and record; a later run that meets the same record writes
  its own row. The next task (loss reporting) reads these.
- `hd_import_run.retry_attempts`: failed tries of the page the run is on. Kept on the run so
  a worker restart doesn't reset it; back to 0 when a page is saved.

Downgrade drops both: the notes go, nothing imported is touched.

Revision ID: 0054
Revises: 0053
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMPORT_SOURCES = (
    "'intercom', 'zendesk', 'freshdesk', 'gorgias', 'helpscout', 'hubspot', 'zoho', 'csv'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_import_run ADD COLUMN retry_attempts integer NOT NULL DEFAULT 0 "
        "CHECK (retry_attempts >= 0)"
    )
    op.execute(
        f"""
        CREATE TABLE hd_import_failure (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            run_id           uuid NOT NULL,
            source           text NOT NULL CHECK (source IN ({_IMPORT_SOURCES})),
            kind             text NOT NULL
                             CHECK (kind IN ('contact', 'ticket', 'article', 'reply')),
            external_id      text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200),
            category         text NOT NULL
                             CHECK (category IN ('not_found', 'temporary', 'incomplete')),
            attempts         integer NOT NULL DEFAULT 1 CHECK (attempts >= 1),
            last_error       text NOT NULL CHECK (length(last_error) <= 500),
            first_failed_at  timestamptz NOT NULL DEFAULT now(),
            last_failed_at   timestamptz NOT NULL DEFAULT now(),
            status           text NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'resolved', 'dismissed')),
            resolved_at      timestamptz,
            resolved_by      uuid,
            CHECK ((status = 'open') = (resolved_at IS NULL)),
            CHECK (status = 'dismissed' OR resolved_by IS NULL),
            UNIQUE (workspace_id, run_id, kind, external_id),
            FOREIGN KEY (workspace_id, run_id)
                REFERENCES hd_import_run (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, resolved_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (resolved_by)
        )
        """
    )
    # A run's failures, newest first (the list an Admin reads).
    op.execute(
        "CREATE INDEX hd_import_failure_run "
        "ON hd_import_failure (workspace_id, run_id, last_failed_at DESC, id)"
    )
    # Resolving: the open failures of a record when it is imported later.
    op.execute(
        "CREATE INDEX hd_import_failure_open "
        "ON hd_import_failure (workspace_id, source, kind, external_id) WHERE status = 'open'"
    )
    for statement in tenant_rls_sql("hd_import_failure"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_import_failure")
    op.execute("ALTER TABLE hd_import_run DROP COLUMN retry_attempts")
