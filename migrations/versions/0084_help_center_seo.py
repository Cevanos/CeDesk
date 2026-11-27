"""Help center SEO controls (feature-gap plan 4.3).

On `hd_article` and on `hd_article_translation` alike (each language has its own page):

- `slug`: cosmetic, 3 to 100 characters of a-z, 0-9 and hyphens. The article's id stays the
  key in a URL (`/help/<id>/<slug>`), so a slug never has to be unique and changing it never
  breaks a link. Empty when the title gives nothing usable (for example a Hindi title).
- `meta_title`, `meta_description`: plain text for the page's title and description tags.
- `canonical_url`: an optional https override. Per row, never inherited: a translation with
  none is its own canonical page, which keeps hreflang valid.

`hd_help_center_settings.seo_indexable`: the help center is `noindex` today; becoming
indexable is an explicit Admin choice, so it starts false.

Revision ID: 0084
Revises: 0083
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hd_article", "hd_article_translation")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"""
            ALTER TABLE {table}
                ADD COLUMN slug text
                    CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) BETWEEN 3 AND 100),
                ADD COLUMN meta_title text CHECK (length(meta_title) BETWEEN 1 AND 120),
                ADD COLUMN meta_description text
                    CHECK (length(meta_description) BETWEEN 1 AND 320),
                ADD COLUMN canonical_url text
                    CHECK (canonical_url ~ '^https://[^[:space:]]+$'
                           AND length(canonical_url) <= 500)
            """
        )
    op.execute(
        "ALTER TABLE hd_help_center_settings "
        "ADD COLUMN seo_indexable boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    # SEO text can be typed again, so nothing here refuses.
    op.execute("ALTER TABLE hd_help_center_settings DROP COLUMN seo_indexable")
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} DROP COLUMN canonical_url, DROP COLUMN meta_description, "
            "DROP COLUMN meta_title, DROP COLUMN slug"
        )
