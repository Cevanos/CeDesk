"""Open analytics (differentiator plan #7): a `reporting` schema of read-only views for
BI tools (Metabase, Power BI, Looker), without personal data.

- `reporting.*` views: tickets, messages, csat, members, teams, team_members,
  ticket_types, workspaces. Numbers, states, times and ids only: no subject or message
  text, no CSAT comment, no names or email addresses of customers or members, no tags or
  custom fields (free text an agent typed may hold personal data).
- `hd_reporting_access`: which reporting login may read which workspace (`workspace_id`
  NULL: every workspace, for a self-hosted server). Filled by `cedesk reporting-user`,
  which runs with the database superuser (creating a login needs it). The app role has
  no rights on it.
- One extra read-only policy per table the views read, for the views' owner (the
  migration role): a row is visible when the session's login is in
  `hd_reporting_access` for its workspace. A view that is not `security_invoker` reads
  with its owner's policies, so a reporting login sees its workspace only, through the
  views only: it has no rights on any `hd_` table. The app and the migration role's own
  sessions are not in the table, so for them nothing changes.

Downgrade drops the views, the policies and the access table. Logins made by
`cedesk reporting-user` stay as roles; drop them with `cedesk reporting-user drop`.

Revision ID: 0080
Revises: 0079
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table -> its workspace column.
TABLES = {
    "hd_ticket": "workspace_id",
    "hd_message": "workspace_id",
    "hd_csat": "workspace_id",
    "member": "workspace_id",
    "hd_team": "workspace_id",
    "hd_team_member": "workspace_id",
    "hd_ticket_type": "workspace_id",
    "workspace": "id",
}

VIEWS = {
    "tickets": """
        SELECT id, workspace_id, number, status, priority, channel, type_id, team_id,
               assignee_id, topic, sentiment, language, source, reopen_count, created_at,
               first_reply_at, resolved_at, updated_at, sla_due_at,
               (sla_alerted_due IS NOT NULL) AS sla_breach_alerted,
               (snoozed_until IS NOT NULL AND snoozed_until > now()) AS snoozed,
               parent_id, merged_into_id
        FROM public.hd_ticket""",
    "messages": """
        SELECT id, workspace_id, ticket_id, kind, author_member_id, created_at
        FROM public.hd_message""",
    "csat": """
        SELECT id, workspace_id, ticket_id, score, source, rated_at
        FROM public.hd_csat""",
    "members": """
        SELECT id, workspace_id, role, role_id, is_active, created_at
        FROM public.member""",
    "teams": """
        SELECT id, workspace_id, name, created_at FROM public.hd_team""",
    "team_members": """
        SELECT workspace_id, team_id, member_id FROM public.hd_team_member""",
    "ticket_types": """
        SELECT id, workspace_id, name FROM public.hd_ticket_type""",
    "workspaces": """
        SELECT id AS workspace_id, name, time_zone, created_at FROM public.workspace""",
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_reporting_access (
            role_name     text PRIMARY KEY CHECK (role_name ~ '^report_[a-z0-9_]{2,40}$'),
            workspace_id  uuid REFERENCES workspace(id) ON DELETE CASCADE,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The migration role's default privileges give the app every new table: not this one.
    op.execute("REVOKE ALL ON hd_reporting_access FROM PUBLIC")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cedesk_app') THEN "
        "REVOKE ALL ON hd_reporting_access FROM cedesk_app; END IF; END $$"
    )
    for table, column in TABLES.items():
        op.execute(
            f"""
            CREATE POLICY {table}_reporting ON {table} FOR SELECT TO CURRENT_USER
            USING (EXISTS (SELECT 1 FROM hd_reporting_access a
                           WHERE a.role_name = session_user
                           AND (a.workspace_id IS NULL OR a.workspace_id = {table}.{column})))
            """  # noqa: S608 (fixed table names)
        )
    op.execute("CREATE SCHEMA reporting")
    op.execute("REVOKE ALL ON SCHEMA reporting FROM PUBLIC")
    for name, query in VIEWS.items():
        op.execute(f"CREATE VIEW reporting.{name} WITH (security_barrier) AS {query}")
        op.execute(f"REVOKE ALL ON reporting.{name} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP SCHEMA reporting CASCADE")
    for table in TABLES:
        op.execute(f"DROP POLICY {table}_reporting ON {table}")
    op.execute("DROP TABLE hd_reporting_access")
