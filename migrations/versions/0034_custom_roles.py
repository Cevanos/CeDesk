"""Custom roles (md A3, A4).

- `hd_role`: a named role an Admin builds from five rights (reply, assign anyone, reports,
  settings, approve) and a scope: `all` tickets, `team_edit` (edit the role's team's
  tickets, view the rest) or `team_only` (the role's team only; other tickets are hidden).
  A scoped role names its team. A team or role in use can't be deleted.
- `member.role` gains `custom`, with `member.role_id` naming the role. Built-in roles have
  no `role_id`.

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILT_IN = "'admin', 'supervisor', 'agent', 'light_agent'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_role (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            reply         boolean NOT NULL DEFAULT true,
            assign_any    boolean NOT NULL DEFAULT false,
            reports       boolean NOT NULL DEFAULT false,
            settings      boolean NOT NULL DEFAULT false,
            approve       boolean NOT NULL DEFAULT false,
            scope         text NOT NULL DEFAULT 'all'
                              CHECK (scope IN ('all', 'team_edit', 'team_only')),
            team_id       uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            CHECK ((scope = 'all') = (team_id IS NULL)),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, team_id)
                REFERENCES hd_team (workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX hd_role_name ON hd_role (workspace_id, lower(name))")
    for statement in tenant_rls_sql("hd_role"):
        op.execute(statement)
    op.execute("ALTER TABLE member ADD COLUMN role_id uuid")
    op.execute(
        "ALTER TABLE member ADD CONSTRAINT member_role_fk FOREIGN KEY (workspace_id, role_id) "
        "REFERENCES hd_role (workspace_id, id) ON DELETE RESTRICT"
    )
    op.execute("ALTER TABLE member DROP CONSTRAINT member_role_check")
    op.execute(
        "ALTER TABLE member ADD CONSTRAINT member_role_check "
        f"CHECK (role IN ({BUILT_IN}, 'custom'))"
    )
    op.execute(
        "ALTER TABLE member ADD CONSTRAINT member_custom_role "
        "CHECK ((role = 'custom') = (role_id IS NOT NULL))"
    )
    op.execute("CREATE INDEX member_role_id ON member (role_id) WHERE role_id IS NOT NULL")


def downgrade() -> None:
    # Members on a custom role go back to Agent, the closest built-in role.
    op.execute("ALTER TABLE member NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE member DROP CONSTRAINT member_custom_role")
    op.execute("UPDATE member SET role = 'agent', role_id = NULL WHERE role = 'custom'")
    op.execute("ALTER TABLE member FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE member DROP CONSTRAINT member_role_check")
    op.execute(f"ALTER TABLE member ADD CONSTRAINT member_role_check CHECK (role IN ({BUILT_IN}))")
    op.execute("DROP INDEX member_role_id")
    op.execute("ALTER TABLE member DROP CONSTRAINT member_role_fk")
    op.execute("ALTER TABLE member DROP COLUMN role_id")
    op.execute("DROP TABLE hd_role")
