"""Files attached to tickets (T18).

The bytes live in the configured storage (local disk or S3); this table holds what we
know about each file. `object_key` is built from ids, never from the file's name.
`message_id` is filled when the file is sent with a reply or note. `scan_status` is the
virus-scan hook: a scanner sets `clean` or `infected`, and an infected file is never
handed out.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_attachment (
            id            uuid PRIMARY KEY,
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            message_id    uuid,
            object_key    text NOT NULL UNIQUE CHECK (length(object_key) BETWEEN 1 AND 512),
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
            size          bigint NOT NULL CHECK (size > 0),
            mime          text NOT NULL CHECK (length(mime) BETWEEN 3 AND 255),
            scan_status   text NOT NULL DEFAULT 'not_scanned'
                          CHECK (scan_status IN ('not_scanned', 'clean', 'infected')),
            uploaded_by   uuid,
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, message_id)
                REFERENCES hd_message (workspace_id, id) ON DELETE SET NULL (message_id),
            FOREIGN KEY (workspace_id, uploaded_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (uploaded_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_attachment_ticket ON hd_attachment (workspace_id, ticket_id, created_at)"
    )
    for statement in tenant_rls_sql("hd_attachment"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_attachment")
