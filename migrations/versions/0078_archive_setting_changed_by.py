"""The archive age gets its own "last changed" (md O3).

The archive age is a column of `hd_retention`, and both settings wrote the same
`updated_by` / `updated_at`. Saving the archive age made the Retention card say retention
had just been changed, by whoever saved the archive age (staging, 23 Nov).

- `hd_retention.archive_updated_by`, `archive_updated_at`: who last saved the archive age,
  and when. `updated_by` / `updated_at` are retention's alone from now on.
- `updated_at` may be NULL: a row the archive age created has no retention change yet.
- Both pairs are filled from each setting's latest change in the audit log (area
  `retention` or `archive`, action `changed`); without one, retention keeps what it has and
  the archive age is left empty, unless it was set (then it takes the row's pair).

Revision ID: 0078
Revises: 0077
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Forced RLS: the owner sees no rows without a workspace, so lift it for the backfill.
_READ = ("hd_retention", "hd_audit", "member")

# Each workspace's latest change of one setting, by a member who still exists.
_LATEST = """
    SELECT DISTINCT ON (a.workspace_id) a.workspace_id, a.at,
           CASE WHEN EXISTS (SELECT 1 FROM member m
                             WHERE m.workspace_id = a.workspace_id AND m.id = a.actor_id)
                THEN a.actor_id END AS actor_id
    FROM hd_audit a WHERE a.area = '{area}' AND a.action = 'changed'
    ORDER BY a.workspace_id, a.seq DESC
"""


def _lift_rls(on: bool) -> None:
    for table in _READ:
        op.execute(f"ALTER TABLE {table} {'' if on else 'NO '}FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hd_retention "
        "ADD COLUMN archive_updated_by uuid, "
        "ADD COLUMN archive_updated_at timestamptz, "
        "ADD FOREIGN KEY (workspace_id, archive_updated_by) "
        "REFERENCES member (workspace_id, id) ON DELETE SET NULL (archive_updated_by), "
        "ALTER COLUMN updated_at DROP NOT NULL, "
        "ALTER COLUMN updated_at DROP DEFAULT"
    )
    _lift_rls(False)
    op.execute(
        f"""
        UPDATE hd_retention r SET archive_updated_by = l.actor_id, archive_updated_at = l.at
        FROM ({_LATEST.format(area="archive")}) l WHERE l.workspace_id = r.workspace_id
        """  # noqa: S608 (a fixed area name)
    )
    op.execute(
        "UPDATE hd_retention SET archive_updated_by = updated_by, archive_updated_at = updated_at "
        "WHERE archive_updated_at IS NULL AND archive_after_days IS NOT NULL"
    )
    op.execute(
        f"""
        UPDATE hd_retention r SET updated_by = l.actor_id, updated_at = l.at
        FROM ({_LATEST.format(area="retention")}) l WHERE l.workspace_id = r.workspace_id
        """  # noqa: S608 (a fixed area name)
    )
    _lift_rls(True)


def downgrade() -> None:
    _lift_rls(False)
    op.execute(
        "UPDATE hd_retention SET "
        "updated_by = CASE WHEN archive_updated_at > coalesce(updated_at, '-infinity') "
        "THEN archive_updated_by ELSE updated_by END, "
        "updated_at = greatest(updated_at, archive_updated_at, "
        "CASE WHEN updated_at IS NULL AND archive_updated_at IS NULL THEN now() END)"
    )
    _lift_rls(True)
    op.execute(
        "ALTER TABLE hd_retention "
        "ALTER COLUMN updated_at SET DEFAULT now(), "
        "ALTER COLUMN updated_at SET NOT NULL, "
        "DROP COLUMN archive_updated_at, "
        "DROP COLUMN archive_updated_by"
    )
