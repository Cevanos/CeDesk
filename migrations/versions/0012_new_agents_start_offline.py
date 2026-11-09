"""New agents start Offline (decision 14.5 #4): nobody gets tickets before they switch
themselves on.

Members who already exist keep working as before: each one without a routing row gets
one saying Available, which is what "no row" meant until now. Light agents get no row
(they have no status).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE hd_agent_state ALTER COLUMN status SET DEFAULT 'offline'")
    # Both tables force RLS, which would hide every row from this cross-workspace copy.
    for table in ("member", "hd_agent_state"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO hd_agent_state (workspace_id, member_id, status) "
        "SELECT m.workspace_id, m.id, 'available' FROM member m "
        "WHERE m.role <> 'light_agent' "
        "ON CONFLICT (workspace_id, member_id) DO NOTHING"
    )
    for table in ("member", "hd_agent_state"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE hd_agent_state ALTER COLUMN status SET DEFAULT 'available'")
