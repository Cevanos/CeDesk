"""Workspace default capacity per channel (md W5), and the team limit of 1-50 (md W1).

An agent's cap_* is now NULL when they follow the workspace default; a number is their
own limit. With no `hd_routing_defaults` row, the md defaults apply: Email 5, Chat 2,
Messaging 3.

Forced RLS applies to the owner too, so the statements that change existing rows lift
it for that table, then force it again.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPS = (("cap_email", 5), ("cap_chat", 2), ("cap_messaging", 3))


def _unforced(table: str, statements: list[str]) -> None:
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for statement in statements:
        op.execute(statement)
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_routing_defaults (
            workspace_id   uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            cap_email      integer NOT NULL DEFAULT 5 CHECK (cap_email BETWEEN 0 AND 100),
            cap_chat       integer NOT NULL DEFAULT 2 CHECK (cap_chat BETWEEN 0 AND 100),
            cap_messaging  integer NOT NULL DEFAULT 3 CHECK (cap_messaging BETWEEN 0 AND 100),
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    for statement in tenant_rls_sql("hd_routing_defaults"):
        op.execute(statement)

    for column, _ in CAPS:
        op.execute(f"ALTER TABLE hd_agent_state ALTER COLUMN {column} DROP NOT NULL")
        op.execute(f"ALTER TABLE hd_agent_state ALTER COLUMN {column} DROP DEFAULT")
    # A stored value equal to the old default was the default: it now follows the workspace.
    _unforced(
        "hd_agent_state",
        [
            # Fixed column names and numbers from CAPS; nothing from input.
            f"UPDATE hd_agent_state SET {column} = NULL WHERE {column} = {default}"  # noqa: S608
            for column, default in CAPS
        ],
    )

    _unforced(
        "hd_team",
        ["UPDATE hd_team SET max_open_per_agent = 50 WHERE max_open_per_agent > 50"],
    )
    op.execute("ALTER TABLE hd_team DROP CONSTRAINT hd_team_max_open_per_agent_check")
    op.execute(
        "ALTER TABLE hd_team ADD CONSTRAINT hd_team_max_open_per_agent_check "
        "CHECK (max_open_per_agent BETWEEN 1 AND 50)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hd_team DROP CONSTRAINT hd_team_max_open_per_agent_check")
    op.execute(
        "ALTER TABLE hd_team ADD CONSTRAINT hd_team_max_open_per_agent_check "
        "CHECK (max_open_per_agent BETWEEN 1 AND 500)"
    )

    # Agents following the workspace default get that default written onto their row.
    op.execute("ALTER TABLE hd_routing_defaults NO FORCE ROW LEVEL SECURITY")
    _unforced(
        "hd_agent_state",
        [
            # Fixed column names and numbers from CAPS; nothing from input.
            f"UPDATE hd_agent_state s SET {column} = coalesce("  # noqa: S608
            f"(SELECT d.{column} FROM hd_routing_defaults d "
            f"WHERE d.workspace_id = s.workspace_id), {default}) WHERE {column} IS NULL"
            for column, default in CAPS
        ],
    )
    for column, default in CAPS:
        op.execute(f"ALTER TABLE hd_agent_state ALTER COLUMN {column} SET DEFAULT {default}")
        op.execute(f"ALTER TABLE hd_agent_state ALTER COLUMN {column} SET NOT NULL")
    op.execute("DROP TABLE hd_routing_defaults")
