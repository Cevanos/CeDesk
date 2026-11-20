"""Backlog snapshots (feature-gap plan 3.1): open tickets over time.

`hd_ticket` only knows what is open now, and keeps no status history, so "how many were open
at the end of last Tuesday" can't be worked out afterwards. The worker therefore writes one
small row per workspace (and per team) each day.

- `hd_backlog_snapshot`: `day` is the workspace's local calendar day the numbers are about:
  the state taken at the first worker pass after that day ended (about its end). `kind` is
  `total` (the whole workspace), `team` (one team, `team_id` set) or `no_team` (tickets with
  no team). One row each per day. A day the worker was down has no row; nothing is filled in.
  History starts the day this ships (no backfill: it would ignore reopens and archived
  tickets). The rows hold counts only, no customer data, so there is nothing to erase.
- A team's rows go when the team is deleted. Rows are pruned after 3 years by the worker.

Revision ID: 0071
Revises: 0070
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_backlog_snapshot (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            day           date NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('total', 'team', 'no_team')),
            team_id       uuid,
            open          integer NOT NULL CHECK (open >= 0),
            pending       integer NOT NULL CHECK (pending >= 0),
            breaching     integer NOT NULL CHECK (breaching >= 0),
            taken_at      timestamptz NOT NULL DEFAULT now(),
            CHECK ((kind = 'team') = (team_id IS NOT NULL)),
            FOREIGN KEY (workspace_id, team_id)
                REFERENCES hd_team (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    # One row per day and kind (and team): `coalesce` because a unique index ignores NULLs.
    op.execute(
        "CREATE UNIQUE INDEX hd_backlog_snapshot_day ON hd_backlog_snapshot "
        "(workspace_id, day, kind, coalesce(team_id, '00000000-0000-0000-0000-000000000000'))"
    )
    op.execute(
        "CREATE INDEX hd_backlog_snapshot_team ON hd_backlog_snapshot "
        "(workspace_id, team_id, day) WHERE team_id IS NOT NULL"
    )
    for statement in tenant_rls_sql("hd_backlog_snapshot"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_backlog_snapshot")
