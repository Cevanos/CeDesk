"""Row-level security for tenant tables.

Every table that holds workspace data gets a `workspace_id` column and, in the
migration that creates it, `enable_tenant_rls(...)`. The guard test
`tests/db/test_tenancy.py` fails if any table with `workspace_id` is missing it.
"""

from cedesk.db import WORKSPACE_SETTING

# NULLIF turns an unset setting into NULL, and `= NULL` is never true: no
# workspace set means no rows, never all rows.
CURRENT_WORKSPACE = f"NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')::uuid"


def tenant_rls_sql(table: str, column: str = "workspace_id") -> list[str]:
    """Statements that enable and force RLS with one policy bound to the current workspace."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_workspace ON {table} "
        f"USING ({column} = {CURRENT_WORKSPACE}) "
        f"WITH CHECK ({column} = {CURRENT_WORKSPACE})",
    ]
