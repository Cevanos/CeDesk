"""SQL for help center categories, articles and votes. RLS applies."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

_VOTES = (
    "(SELECT count(*) FROM hd_article_vote v WHERE v.article_id = a.id AND v.helpful) "
    "AS helpful_count, "
    "(SELECT count(*) FROM hd_article_vote v WHERE v.article_id = a.id AND NOT v.helpful) "
    "AS not_helpful_count"
)
_ARTICLE = (  # built from the constants above only
    "SELECT a.id, a.category_id, c.name AS category_name, a.title, a.body, a.status, "  # noqa: S608
    "a.source, a.published_at, a.created_at, a.updated_at, m.name AS updated_by_name, "
    + _VOTES
    + " FROM hd_article a LEFT JOIN hd_article_category c ON c.id = a.category_id "
    "LEFT JOIN member m ON m.id = a.updated_by "
)


# Categories


async def categories(session: AsyncSession, *, published_only: bool) -> Sequence[RowMapping]:
    """With the number of articles in each (only published ones for the public)."""
    result = await session.execute(
        text(
            "SELECT c.id, c.name, c.position, c.source, c.created_at, "
            "(SELECT count(*) FROM hd_article a WHERE a.category_id = c.id "
            "AND (NOT :published_only OR a.status = 'published')) AS article_count "
            "FROM hd_article_category c ORDER BY c.position, c.name"
        ),
        {"published_only": published_only},
    )
    return result.mappings().all()


async def category(session: AsyncSession, category_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text("SELECT id, name, position FROM hd_article_category WHERE id = :id"),
        {"id": category_id},
    )
    return result.mappings().first()


async def insert_category(
    session: AsyncSession, workspace_id: UUID, name: str, position: int
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_article_category (workspace_id, name, position) "
            "VALUES (:ws, :name, :position) RETURNING id"
        ),
        {"ws": workspace_id, "name": name, "position": position},
    )
    found: UUID = result.scalar_one()
    return found


async def update_category(
    session: AsyncSession, category_id: UUID, name: str, position: int
) -> bool:
    result = await session.execute(
        text(
            "UPDATE hd_article_category SET name = :name, position = :position "
            "WHERE id = :id RETURNING id"
        ),
        {"id": category_id, "name": name, "position": position},
    )
    return result.first() is not None


async def delete_category(session: AsyncSession, category_id: UUID) -> bool:
    """Its articles stay, with no category."""
    result = await session.execute(
        text("DELETE FROM hd_article_category WHERE id = :id RETURNING id"), {"id": category_id}
    )
    return result.first() is not None


# Articles


async def article(
    session: AsyncSession, article_id: UUID, *, published_only: bool = False
) -> RowMapping | None:
    where = " AND a.status = 'published'" if published_only else ""
    result = await session.execute(text(_ARTICLE + "WHERE a.id = :id" + where), {"id": article_id})
    return result.mappings().first()


async def list_articles(
    session: AsyncSession,
    *,
    status: str | None,
    category_id: UUID | None,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Newest first."""
    result = await session.execute(
        text(
            _ARTICLE + "WHERE (CAST(:status AS text) IS NULL OR a.status = :status) "
            "AND (CAST(:category AS uuid) IS NULL OR a.category_id = :category) "
            "AND (CAST(:at AS timestamptz) IS NULL OR (a.created_at, a.id) < (:at, :id)) "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit"
        ),
        {
            "status": status,
            "category": category_id,
            "at": before[0] if before else None,
            "id": before[1] if before else None,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def search(
    session: AsyncSession,
    query: str,
    *,
    status: str | None,
    category_id: UUID | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Best match first (a title match counts more than one in the body)."""
    result = await session.execute(
        text(
            _ARTICLE + "WHERE a.search @@ to_tsquery('simple', :q) "
            "AND (CAST(:status AS text) IS NULL OR a.status = :status) "
            "AND (CAST(:category AS uuid) IS NULL OR a.category_id = :category) "
            "ORDER BY ts_rank(a.search, to_tsquery('simple', :q)) DESC, a.created_at DESC "
            "LIMIT :limit"
        ),
        {"q": query, "status": status, "category": category_id, "limit": limit},
    )
    return result.mappings().all()


async def insert_article(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    category_id: UUID | None,
    title: str,
    body: str,
    status: str,
    member_id: UUID,
) -> UUID:
    result = await session.execute(
        text(
            "INSERT INTO hd_article (workspace_id, category_id, title, body, status, "
            "updated_by, published_at) VALUES (:ws, :category, :title, :body, :status, :by, "
            "CASE WHEN :status = 'published' THEN now() END) RETURNING id"
        ),
        {
            "ws": workspace_id,
            "category": category_id,
            "title": title,
            "body": body,
            "status": status,
            "by": member_id,
        },
    )
    found: UUID = result.scalar_one()
    return found


async def update_article(
    session: AsyncSession,
    article_id: UUID,
    *,
    category_id: UUID | None,
    title: str,
    body: str,
    status: str,
    member_id: UUID,
) -> None:
    """`published_at` is the first time it went live; unpublishing keeps it."""
    await session.execute(
        text(
            "UPDATE hd_article SET category_id = :category, title = :title, body = :body, "
            "status = :status, updated_by = :by, updated_at = now(), "
            "published_at = CASE WHEN :status = 'published' "
            "THEN coalesce(published_at, now()) ELSE published_at END WHERE id = :id"
        ),
        {
            "id": article_id,
            "category": category_id,
            "title": title,
            "body": body,
            "status": status,
            "by": member_id,
        },
    )


async def lock_article(session: AsyncSession, article_id: UUID) -> RowMapping | None:
    result = await session.execute(
        text(
            "SELECT id, category_id, title, body, status FROM hd_article WHERE id = :id FOR UPDATE"
        ),
        {"id": article_id},
    )
    return result.mappings().first()


async def delete_article(session: AsyncSession, article_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM hd_article WHERE id = :id RETURNING id"), {"id": article_id}
    )
    return result.first() is not None


# Votes


async def vote(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    article_id: UUID,
    visitor_hash: bytes,
    helpful: bool,
) -> None:
    await session.execute(
        text(
            "INSERT INTO hd_article_vote (workspace_id, article_id, visitor_hash, helpful) "
            "VALUES (:ws, :article, :visitor, :helpful) "
            "ON CONFLICT (workspace_id, article_id, visitor_hash) "
            "DO UPDATE SET helpful = EXCLUDED.helpful, voted_at = now()"
        ),
        {"ws": workspace_id, "article": article_id, "visitor": visitor_hash, "helpful": helpful},
    )
