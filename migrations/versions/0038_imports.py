"""Imports from other help desks (md M1-M10, section 8).

- `hd_import_connection`: one per source an Admin connected (or CSV file uploaded). The
  source's credentials are sealed (AES-GCM, `cedesk.crypto`) and are deleted (set NULL)
  when the run finishes without "keep syncing", or when syncing is stopped. `settings`
  holds what is not secret (a subdomain, the CSV file's storage key).
- `hd_import_run`: one run of a connection. `options` (what to bring, status map, group to
  team), `cursor` (where to resume after a crash or a pause), `counts`, `sync_on` (keep
  syncing until go-live, M9), and the lease the worker takes (`lease_until`) so two
  workers never work the same run.
- `hd_import_map`: the CeDesk record for each source record that has no `source`/`source_id`
  column of its own (contacts, categories, articles, saved replies). Tickets use
  `hd_ticket (source, source_id)`. Running an import again finds these and skips them.
- `hd_import_event`: connect, start, finish, fail, stop sync, credentials deleted. The
  import history (M10) and the audit trail for imports.
- `hd_csat.source` also accepts the import sources, so an imported rating keeps where it
  came from. Downgrade deletes imported ratings (the old rule can't hold them), as 0032's
  downgrade removes the rules its old check can't hold; the rest of the downgrade drops
  these four tables.

Revision ID: 0038
Revises: 0037
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_import_connection", "hd_import_run", "hd_import_map", "hd_import_event")
_IMPORT_SOURCES = (
    "'intercom', 'zendesk', 'freshdesk', 'gorgias', 'helpscout', 'hubspot', 'zoho', 'csv'"
)
_CSAT_SOURCES_OLD = "'portal', 'email'"
_CSAT_SOURCES_NEW = f"'portal', 'email', {_IMPORT_SOURCES}"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE hd_import_connection (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id        uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            source              text NOT NULL CHECK (source IN ({_IMPORT_SOURCES})),
            label               text NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
            settings            jsonb NOT NULL DEFAULT '{{}}'
                                CHECK (jsonb_typeof(settings) = 'object'),
            credentials_sealed  bytea,
            credentials_hint    text CHECK (length(credentials_hint) <= 8),
            state               text NOT NULL DEFAULT 'connected'
                                CHECK (state IN ('connected', 'closed')),
            created_by          uuid,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE hd_import_run (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            connection_id  uuid NOT NULL,
            source         text NOT NULL CHECK (source IN ({_IMPORT_SOURCES})),
            state          text NOT NULL DEFAULT 'queued'
                           CHECK (state IN ('queued', 'running', 'done', 'failed')),
            options        jsonb NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(options) = 'object'),
            cursor         jsonb NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(cursor) = 'object'),
            counts         jsonb NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(counts) = 'object'),
            sync_on        boolean NOT NULL DEFAULT false,
            last_sync_at   timestamptz,
            next_run_at    timestamptz NOT NULL DEFAULT now(),
            lease_until    timestamptz,
            last_error     text CHECK (length(last_error) <= 500),
            started_by     uuid,
            started_at     timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, connection_id)
                REFERENCES hd_import_connection (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, started_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (started_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_import_run_due ON hd_import_run (next_run_at) "
        "WHERE state IN ('queued', 'running')"
    )
    op.execute(
        "CREATE INDEX hd_import_run_connection ON hd_import_run (workspace_id, connection_id)"
    )
    op.execute(
        """
        CREATE TABLE hd_import_map (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            source        text NOT NULL CHECK (source ~ '^[a-z][a-z0-9_-]{0,39}$'),
            kind          text NOT NULL
                          CHECK (kind IN ('contact', 'category', 'article', 'reply')),
            external_id   text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200),
            local_id      uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, source, kind, external_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE hd_import_event (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id   uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            connection_id  uuid NOT NULL,
            run_id         uuid,
            kind           text NOT NULL CHECK (kind IN (
                               'connected', 'started', 'finished', 'failed', 'sync_stopped',
                               'credentials_deleted')),
            member_id      uuid,
            detail         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
            -- clock_timestamp, not now(): events written in one transaction keep their order.
            created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (workspace_id, connection_id)
                REFERENCES hd_import_connection (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, run_id)
                REFERENCES hd_import_run (workspace_id, id) ON DELETE SET NULL (run_id),
            FOREIGN KEY (workspace_id, member_id)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (member_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_import_event_recent ON hd_import_event (workspace_id, created_at DESC)"
    )
    for table in TABLES:
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    _csat_sources(_CSAT_SOURCES_NEW)


def _csat_sources(allowed: str) -> None:
    op.execute("ALTER TABLE hd_csat DROP CONSTRAINT IF EXISTS hd_csat_source_check")
    op.execute(
        f"ALTER TABLE hd_csat ADD CONSTRAINT hd_csat_source_check CHECK (source IN ({allowed}))"
    )


def downgrade() -> None:
    # The old rule can't hold an imported rating, so they go (as 0032 removes the rules its
    # old check can't hold). Forced RLS: the owner sees no rows without a workspace, so lift
    # it for the delete.
    op.execute("ALTER TABLE hd_csat NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM hd_csat WHERE source NOT IN ('portal', 'email')")
    op.execute("ALTER TABLE hd_csat FORCE ROW LEVEL SECURITY")
    _csat_sources(_CSAT_SOURCES_OLD)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
