"""Help center (md P6): categories, articles with full-text search, and "Was this
helpful?" votes.

- `hd_article_category`: name unique per workspace, position. `source`/`source_id` mark
  imported categories (M8).
- `hd_article`: draft or published; body is Markdown. `search` is a generated tsvector
  (title weighted above body, the `simple` config so Hindi and English both work) with a
  GIN index. `source`/`source_id` for imports, unique per source.
- `hd_article_vote`: one vote per visitor per article (the visitor id is the browser's
  random id, stored only as a SHA-256), and it can be changed.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

from alembic import op

from cedesk.tenancy import tenant_rls_sql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hd_article_category (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            position      integer NOT NULL DEFAULT 0,
            source        text CHECK (source ~ '^[a-z][a-z0-9_-]{0,39}$'),
            source_id     text CHECK (length(source_id) <= 200),
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, name)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX hd_article_category_source ON hd_article_category "
        "(workspace_id, source, source_id) WHERE source IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE hd_article (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            category_id   uuid,
            title         text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            body          text NOT NULL DEFAULT '' CHECK (length(body) <= 100000),
            status        text NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'published')),
            source        text CHECK (source ~ '^[a-z][a-z0-9_-]{0,39}$'),
            source_id     text CHECK (length(source_id) <= 200),
            updated_by    uuid,
            published_at  timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            search        tsvector GENERATED ALWAYS AS (
                              setweight(to_tsvector('simple', title), 'A')
                              || setweight(to_tsvector('simple', body), 'B')
                          ) STORED,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, category_id)
                REFERENCES hd_article_category (workspace_id, id) ON DELETE SET NULL (category_id),
            FOREIGN KEY (workspace_id, updated_by)
                REFERENCES member (workspace_id, id) ON DELETE SET NULL (updated_by)
        )
        """
    )
    op.execute("CREATE INDEX hd_article_search ON hd_article USING gin (search)")
    op.execute("CREATE INDEX hd_article_list ON hd_article (workspace_id, status, created_at, id)")
    op.execute(
        "CREATE UNIQUE INDEX hd_article_source ON hd_article "
        "(workspace_id, source, source_id) WHERE source IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE hd_article_vote (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            article_id    uuid NOT NULL,
            visitor_hash  bytea NOT NULL,
            helpful       boolean NOT NULL,
            voted_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, article_id, visitor_hash),
            FOREIGN KEY (workspace_id, article_id)
                REFERENCES hd_article (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    for table in ("hd_article_category", "hd_article", "hd_article_vote"):
        for statement in tenant_rls_sql(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE hd_article_vote")
    op.execute("DROP TABLE hd_article")
    op.execute("DROP TABLE hd_article_category")
