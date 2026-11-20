"""Index for the report engine (feature-gap plan 3.1, 3.2).

The reports filter and group tickets by when they were created. The existing created-time
index is on an epoch expression (it serves the ticket list's sort), so a plain
`created_at >= ...` range can't use it.

- `hd_ticket_created_range (workspace_id, created_at)`

Measured with `scripts/bench_reports.py` at 100k tickets in one workspace: every report case
runs in under 400 ms (p95) and this index is the one the planner picks. A `type_id` index was
tried and left out: nothing used it, and every ticket insert would pay for it.

Plain `CREATE INDEX`, like every earlier `hd_ticket` index: the archive keeps the table to
open tickets and recently resolved ones, so the build is short.

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX hd_ticket_created_range ON hd_ticket (workspace_id, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX hd_ticket_created_range")
