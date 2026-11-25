"""Custom domain for the help center (feature-gap plan 4.2).

- `hd_help_domain`: at most one per workspace (the primary key). A domain starts `pending`; it
  becomes `verified` when the Admin has put the workspace's token in a TXT record at
  `_cedesk-verify.<domain>`. Several workspaces may hold the same domain as pending, so a
  stranger cannot block the real owner by registering it first; the unique index is on
  verified domains only, so the first to prove control wins.
- `help_domain_directory`: global, with no row-level security, like `workspace_directory` and
  `email_address_directory` (0017): the public side must find the workspace from a host name
  before it knows the workspace. It holds verified domains only and the app role cannot write
  to it: a trigger on `hd_help_domain` (SECURITY DEFINER) keeps it in step, so it can never
  drift from the table it mirrors.

Going down drops both tables: domains are typed again and proven again, so nothing refuses.

Revision ID: 0085
Revises: 0084
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_help_domain (
            workspace_id     uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            domain           text NOT NULL CHECK (
                                 domain = lower(domain) AND length(domain) BETWEEN 4 AND 253),
            status           text NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'verified')),
            verify_token     text NOT NULL CHECK (length(verify_token) BETWEEN 20 AND 100),
            verified_at      timestamptz,
            last_checked_at  timestamptz,
            failed_checks    integer NOT NULL DEFAULT 0 CHECK (failed_checks >= 0),
            last_error       text CHECK (length(last_error) <= 300),
            created_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            CHECK ((status = 'verified') = (verified_at IS NOT NULL)),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_help_domain_verified ON hd_help_domain (domain) "
        "WHERE status = 'verified'"
    )
    for statement in tenant_rls_sql("hd_help_domain"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE help_domain_directory (
            domain        text PRIMARY KEY CHECK (domain = lower(domain)),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON help_domain_directory FROM cedesk_app")
    op.execute("GRANT SELECT ON help_domain_directory TO cedesk_app")
    op.execute(
        """
        CREATE FUNCTION hd_help_domain_to_directory() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status = 'verified' THEN
                    DELETE FROM help_domain_directory
                    WHERE domain = OLD.domain AND workspace_id = OLD.workspace_id;
                END IF;
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'verified'
               AND (NEW.status <> 'verified' OR NEW.domain <> OLD.domain) THEN
                DELETE FROM help_domain_directory
                WHERE domain = OLD.domain AND workspace_id = OLD.workspace_id;
            END IF;
            IF NEW.status = 'verified'
               AND (TG_OP = 'INSERT' OR OLD.status <> 'verified' OR OLD.domain <> NEW.domain) THEN
                INSERT INTO help_domain_directory (domain, workspace_id)
                VALUES (NEW.domain, NEW.workspace_id);
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION hd_help_domain_to_directory() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER hd_help_domain_directory AFTER INSERT OR UPDATE OR DELETE "
        "ON hd_help_domain FOR EACH ROW EXECUTE FUNCTION hd_help_domain_to_directory()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER hd_help_domain_directory ON hd_help_domain")
    op.execute("DROP FUNCTION hd_help_domain_to_directory()")
    op.execute("DROP TABLE help_domain_directory")
    op.execute("DROP TABLE hd_help_domain")
