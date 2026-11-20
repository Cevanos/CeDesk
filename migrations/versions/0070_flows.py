"""Workflows (feature-gap plan 5.2, 5.3): the visual builder's flows.

- `hd_flow`: a named graph (trigger, condition branches, actions, waits; checked by the
  service before saving), switched on or off, with run limits like a rule's. `trigger`
  repeats the trigger node's event, so a ticket event finds its flows with one lookup.
  `version` goes up by one each time the graph changes.
- `hd_flow_version`: every graph a flow has had. A run keeps the version it started on,
  so a flow changed while runs wait does not change what those runs do next.
- `hd_automation_run.flow_id` points at the flow (set to null when the flow is deleted;
  the service cancels its waiting runs first).

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = "'created', 'customer_replied', 'sla_breached', 'snooze_ended'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE hd_flow (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name               text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            enabled            boolean NOT NULL DEFAULT false,
            trigger            text NOT NULL CHECK (trigger IN ({EVENTS})),
            version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            graph              jsonb NOT NULL CHECK (jsonb_typeof(graph) = 'object'),
            max_runs_per_hour  integer NOT NULL DEFAULT 1000
                               CHECK (max_runs_per_hour BETWEEN 1 AND 100000),
            max_runs_per_day   integer NOT NULL DEFAULT 10000
                               CHECK (max_runs_per_day BETWEEN 1 AND 1000000),
            created_by         uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX hd_flow_name ON hd_flow (workspace_id, lower(name))")
    op.execute("CREATE INDEX hd_flow_trigger ON hd_flow (workspace_id, trigger) WHERE enabled")
    op.execute(
        """
        CREATE TABLE hd_flow_version (
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            flow_id       uuid NOT NULL,
            version       integer NOT NULL CHECK (version >= 1),
            graph         jsonb NOT NULL CHECK (jsonb_typeof(graph) = 'object'),
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, flow_id, version),
            FOREIGN KEY (workspace_id, flow_id)
                REFERENCES hd_flow (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, created_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    for table in ("hd_flow", "hd_flow_version"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute(
        "ALTER TABLE hd_automation_run ADD CONSTRAINT hd_automation_run_flow_fk "
        "FOREIGN KEY (workspace_id, flow_id) REFERENCES hd_flow (workspace_id, id) "
        "ON DELETE SET NULL (flow_id)"
    )


def downgrade() -> None:
    # Flow runs go with the flows (0069's table outlives them).
    op.execute("ALTER TABLE hd_automation_run NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "DELETE FROM hd_automation_run WHERE flow_id IS NOT NULL OR flow_version IS NOT NULL"
    )
    op.execute("ALTER TABLE hd_automation_run FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE hd_automation_run DROP CONSTRAINT hd_automation_run_flow_fk")
    op.execute("DROP TABLE hd_flow_version")
    op.execute("DROP TABLE hd_flow")
