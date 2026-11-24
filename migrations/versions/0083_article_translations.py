"""Help center languages (feature-gap plan 4.1).

- `hd_help_center_settings`: one row per workspace, made the first time it is needed. A
  missing row means the default language is `en`. The article rows stay the default-language
  version; every other language is a row in `hd_article_translation`.
- `hd_article_translation`: one row per article and language. `locale` is stored lower-case
  (`pt-br`). Visible to the public only when both the article and the translation are
  published. `search` is built like the article's (`simple` config, title weighted above body).
- `hd_article_category.name_translations`: `{locale: name}`, so a Hindi page does not show
  English category names. A column, not a table: there are few categories.
- `hd_article_vote.locale`: the language of the page that was voted on (last vote wins). Not
  part of the unique key: a visitor still has one vote per article.

Going below 0083 is refused while a translation exists: dropping the table would lose
them. Delete them on purpose first (nothing is changed when it refuses).

Revision ID: 0083
Revises: 0082
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCALE = "^[a-z]{2,3}(-[a-z0-9]{2,8})?$"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE hd_help_center_settings (
            workspace_id    uuid PRIMARY KEY REFERENCES workspace(id) ON DELETE CASCADE,
            default_locale  text NOT NULL DEFAULT 'en' CHECK (default_locale ~ '{LOCALE}'),
            updated_by      uuid,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE hd_article_translation (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            article_id    uuid NOT NULL,
            locale        text NOT NULL CHECK (locale ~ '{LOCALE}'),
            title         text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            body          text NOT NULL DEFAULT '' CHECK (length(body) <= 100000),
            status        text NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'published')),
            updated_by    uuid,
            published_at  timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            search        tsvector GENERATED ALWAYS AS (
                              setweight(to_tsvector('simple', title), 'A')
                              || setweight(to_tsvector('simple', body), 'B')
                          ) STORED,
            UNIQUE (workspace_id, article_id, locale),
            FOREIGN KEY (workspace_id, article_id)
                REFERENCES hd_article (workspace_id, id) ON DELETE CASCADE,
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute(
        "CREATE INDEX hd_article_translation_search ON hd_article_translation USING gin (search)"
    )
    op.execute(
        "CREATE INDEX hd_article_translation_locale ON hd_article_translation "
        "(workspace_id, locale, status)"
    )
    for table in ("hd_help_center_settings", "hd_article_translation"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)
    op.execute(
        "ALTER TABLE hd_article_category ADD COLUMN name_translations jsonb NOT NULL "
        "DEFAULT '{}' CHECK (jsonb_typeof(name_translations) = 'object')"
    )
    op.execute(f"ALTER TABLE hd_article_vote ADD COLUMN locale text CHECK (locale ~ '{LOCALE}')")


def downgrade() -> None:
    # Forced RLS: the owner sees no rows without a workspace, so lift it to count them.
    op.execute("ALTER TABLE hd_article_translation NO FORCE ROW LEVEL SECURITY")
    count = (
        op.get_bind().execute(sa.text("SELECT count(*) FROM hd_article_translation")).scalar_one()
    )
    if count:
        raise RuntimeError(
            f"hd_article_translation holds {count} translations; downgrading 0083 would "
            "delete them. Delete them on purpose first (nothing was changed)."
        )
    op.execute("ALTER TABLE hd_article_vote DROP COLUMN locale")
    op.execute("ALTER TABLE hd_article_category DROP COLUMN name_translations")
    op.execute("DROP TABLE hd_article_translation")
    op.execute("DROP TABLE hd_help_center_settings")
