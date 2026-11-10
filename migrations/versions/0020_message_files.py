"""Files on replies, notes and customer emails (md T18).

`hd_attachment.message_id` (from 0011) is now filled: when an agent sends uploaded files
with a reply or note, and when a customer's email brings files. This index lets every
message list its files.

A reply or note may now be only files, so `hd_message.body` may be empty. The service
still refuses a message with neither text nor files.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_message DROP CONSTRAINT hd_message_body_check")
    op.execute(
        "ALTER TABLE hd_message ADD CONSTRAINT hd_message_body_check CHECK (length(body) <= 50000)"
    )
    op.execute(
        "CREATE INDEX hd_attachment_message ON hd_attachment (workspace_id, message_id, "
        "created_at) WHERE message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX hd_attachment_message")
    op.execute("ALTER TABLE hd_message NO FORCE ROW LEVEL SECURITY")  # see every workspace
    op.execute("UPDATE hd_message SET body = '(files)' WHERE body = ''")
    op.execute("ALTER TABLE hd_message FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_message DROP CONSTRAINT hd_message_body_check")
    op.execute(
        "ALTER TABLE hd_message ADD CONSTRAINT hd_message_body_check "
        "CHECK (length(body) BETWEEN 1 AND 50000)"
    )
