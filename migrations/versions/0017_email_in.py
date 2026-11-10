"""Email in (md C8, 7.4).

`email_address_directory`: global, like `workspace_directory`. An incoming email names
only the address it was sent to, so this maps a support address to its workspace before
any workspace is known. It holds no workspace data, so it has no RLS (listed in the
tenancy guard's GLOBAL_TABLES). The app can only read it: triggers on `hd_email_address`
(running as the table owner) add and remove its rows.

`hd_message_email_id`: finds the message an email answers (In-Reply-To / References
match `email->>'message_id'`), and spots an email that arrives twice.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE email_address_directory (
            address       text PRIMARY KEY CHECK (address = lower(address)),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON email_address_directory FROM cedesk_app")
    op.execute("GRANT SELECT ON email_address_directory TO cedesk_app")
    op.execute(
        """
        CREATE FUNCTION hd_email_address_to_directory() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO email_address_directory (address, workspace_id)
                VALUES (NEW.address, NEW.workspace_id);
                RETURN NEW;
            END IF;
            DELETE FROM email_address_directory
            WHERE address = OLD.address AND workspace_id = OLD.workspace_id;
            RETURN OLD;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION hd_email_address_to_directory() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER hd_email_address_directory AFTER INSERT OR DELETE ON hd_email_address "
        "FOR EACH ROW EXECUTE FUNCTION hd_email_address_to_directory()"
    )
    # Addresses are never renamed (the API has no way to), so UPDATE needs no trigger;
    # this makes sure nothing else renames one either.
    op.execute(
        """
        CREATE FUNCTION hd_email_address_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.address <> OLD.address OR NEW.workspace_id <> OLD.workspace_id THEN
                RAISE EXCEPTION 'an email address cannot be changed; add a new one';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER hd_email_address_fixed BEFORE UPDATE ON hd_email_address "
        "FOR EACH ROW EXECUTE FUNCTION hd_email_address_fixed()"
    )
    # Addresses added before this migration (RLS lifted for the copy only).
    op.execute("ALTER TABLE hd_email_address NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO email_address_directory (address, workspace_id) "
        "SELECT address, workspace_id FROM hd_email_address"
    )
    op.execute("ALTER TABLE hd_email_address FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE INDEX hd_message_email_id ON hd_message (workspace_id, (email->>'message_id')) "
        "WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_message_email_id")
    op.execute("DROP TRIGGER hd_email_address_fixed ON hd_email_address")
    op.execute("DROP FUNCTION hd_email_address_fixed()")
    op.execute("DROP TRIGGER hd_email_address_directory ON hd_email_address")
    op.execute("DROP FUNCTION hd_email_address_to_directory()")
    op.execute("DROP TABLE email_address_directory")
