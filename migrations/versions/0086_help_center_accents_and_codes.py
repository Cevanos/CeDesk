"""Help center: search that ignores accents, and three-part language codes.

- Search: `cafe` finds "Café" and `oublie` finds "oublié". The `search` column of `hd_article`
  and `hd_article_translation` is rebuilt over `cedesk_unaccent(text)`, an immutable wrapper
  around the `unaccent` extension (a generated column needs an immutable function), and the
  queries fold what the customer typed the same way. Letters of scripts that have no accent
  rules (Hindi, Arabic, Japanese, ...) are left as they are.
- Language codes: a language then at most a script and a region (`zh-hant-tw`, `sr-latn-rs`),
  in the three places a code is stored.

Going back refuses while a three-part code is stored (nothing is changed), like 0083 does for
translations.

Revision ID: 0086
Revises: 0085
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = "^[a-z]{2,3}(-[a-z0-9]{2,8}){0,2}$"
OLD = "^[a-z]{2,3}(-[a-z0-9]{2,8})?$"
# table, column, the name Postgres gave the column's CHECK in 0083.
CODES = (
    ("hd_help_center_settings", "default_locale"),
    ("hd_article_translation", "locale"),
    ("hd_article_vote", "locale"),
)
SEARCH = (
    ("hd_article", "hd_article_search"),
    ("hd_article_translation", "hd_article_translation_search"),
)


def _codes(pattern: str) -> None:
    for table, column in CODES:
        name = f"{table}_{column}_check"
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} ~ '{pattern}')")


def _search(expression: str) -> None:
    for table, index in SEARCH:
        op.execute(f"DROP INDEX IF EXISTS {index}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN search")
        op.execute(
            f"""
            ALTER TABLE {table} ADD COLUMN search tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('simple', {expression.format(col="title")}), 'A')
                || setweight(to_tsvector('simple', {expression.format(col="body")}), 'B')
            ) STORED
            """
        )
        op.execute(f"CREATE INDEX {index} ON {table} USING gin (search)")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        """
        CREATE FUNCTION cedesk_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        SET search_path = pg_catalog, public
        AS $$ SELECT unaccent('unaccent'::regdictionary, $1) $$
        """
    )
    _search("cedesk_unaccent({col})")
    _codes(NEW)


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it to count them, and
    # put it back only where it was on.
    bind = op.get_bind()
    tables = [table for table, _ in CODES]
    forced = [
        table
        for table in tables
        if bind.execute(
            sa.text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
            {"t": table},
        ).scalar_one()
    ]
    for table in forced:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    long = 0
    for table, column in CODES:
        long += bind.execute(
            # Table and column names come from the CODES constant above, never from input.
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} !~ :p"),  # noqa: S608
            {"p": OLD},
        ).scalar_one()
    for table in forced:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if long:
        raise RuntimeError(
            f"{long} stored language code(s) have three parts (like zh-hant-tw); downgrading "
            "0086 would break them. Change or delete them first (nothing was changed)."
        )
    _codes(OLD)
    _search("{col}")
    op.execute("DROP FUNCTION cedesk_unaccent(text)")
