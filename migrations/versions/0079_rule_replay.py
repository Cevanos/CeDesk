"""Replays: a rule or workflow tried on past tickets (differentiator plan #3).

- `hd_rule_replay`: one row per replay an Admin asks for. A saved rule or workflow
  (`target_id`, and for a workflow the `target_version` tried) or an unsaved one
  (`draft`), tried on the workspace's newest `ticket_limit` tickets (1-1000). The worker
  goes through them in pages, keeping its place (`cursor_at`, `cursor_id`) and the counts,
  so the Admin sees progress and a crash carries on. Nothing on a ticket changes; nothing
  is sent.
- `samples`: up to 200 tickets it would change (number, subject, the changes);
  `conflict_samples`: up to 200 places where another enabled rule sets the same field to a
  different value. Short values only: never a message's text.
- At most one queued or running replay per workspace (a partial unique index).
- Finished replays are kept 30 days (the worker's sweep).

Revision ID: 0079
Revises: 0078
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_rule_replay (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id      uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            kind              text NOT NULL CHECK (kind IN ('rule', 'flow')),
            target_id         uuid,
            target_version    integer CHECK (target_version >= 1),
            name              text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            draft             jsonb CHECK (draft IS NULL OR jsonb_typeof(draft) = 'object'),
            ticket_limit      integer NOT NULL CHECK (ticket_limit BETWEEN 1 AND 1000),
            state             text NOT NULL DEFAULT 'queued' CHECK (state IN
                                  ('queued', 'running', 'done', 'failed', 'cancelled')),
            checked           integer NOT NULL DEFAULT 0 CHECK (checked >= 0),
            matched           integer NOT NULL DEFAULT 0 CHECK (matched >= 0),
            would_change      integer NOT NULL DEFAULT 0 CHECK (would_change >= 0),
            would_wait        integer NOT NULL DEFAULT 0 CHECK (would_wait >= 0),
            conflicts         integer NOT NULL DEFAULT 0 CHECK (conflicts >= 0),
            samples           jsonb NOT NULL DEFAULT '[]'
                                  CHECK (jsonb_typeof(samples) = 'array'),
            conflict_samples  jsonb NOT NULL DEFAULT '[]'
                                  CHECK (jsonb_typeof(conflict_samples) = 'array'),
            cursor_at         timestamptz,
            cursor_id         uuid,
            lease_until       timestamptz,
            error             text CHECK (length(error) <= 300),
            requested_by      uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            started_at        timestamptz,
            finished_at       timestamptz,
            UNIQUE (workspace_id, id),
            CHECK (target_id IS NOT NULL OR draft IS NOT NULL),
            FOREIGN KEY (workspace_id, requested_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (requested_by)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_rule_replay_one_active ON hd_rule_replay (workspace_id) "
        "WHERE state IN ('queued', 'running')"
    )
    op.execute(
        "CREATE INDEX hd_rule_replay_recent ON hd_rule_replay (workspace_id, created_at DESC)"
    )
    for statement in tenant_rls_sql("hd_rule_replay"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_rule_replay")
