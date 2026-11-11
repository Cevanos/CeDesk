"""CSAT: customers rate a resolved ticket 1-5 with an optional comment (md P5, T15).

- `hd_csat`: one rating per ticket (the latest counts: a customer may change it), from
  the portal or the emailed link.
- `hd_csat_link`: the emailed survey link. Only the token's SHA-256 is stored; it lasts
  30 days. `effect_key` (the outbox event + handler) is unique, so a retried event never
  sends a second survey.
- `hd_portal_settings.csat_survey`: email the survey when a ticket is resolved (on by
  default).

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_csat (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            contact_id    uuid NOT NULL,
            score         smallint NOT NULL CHECK (score BETWEEN 1 AND 5),
            comment       text CHECK (length(comment) <= 2000),
            source        text NOT NULL CHECK (source IN ('portal', 'email')),
            rated_at      timestamptz NOT NULL DEFAULT now(),
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, ticket_id),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, contact_id)
                REFERENCES hd_contact (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_csat_contact ON hd_csat (workspace_id, contact_id)")
    op.execute("CREATE INDEX hd_csat_rated ON hd_csat (workspace_id, rated_at)")
    op.execute(
        """
        CREATE TABLE hd_csat_link (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            ticket_id     uuid NOT NULL,
            token_hash    bytea NOT NULL UNIQUE,
            effect_key    text NOT NULL UNIQUE CHECK (length(effect_key) <= 200),
            expires_at    timestamptz NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, ticket_id)
                REFERENCES hd_ticket (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX hd_csat_link_ticket ON hd_csat_link (workspace_id, ticket_id)")
    for table in ("hd_csat", "hd_csat_link"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute(
        "ALTER TABLE hd_portal_settings ADD COLUMN csat_survey boolean NOT NULL DEFAULT true"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_portal_settings DROP COLUMN csat_survey")
    op.execute("DROP TABLE hd_csat_link")
    op.execute("DROP TABLE hd_csat")
