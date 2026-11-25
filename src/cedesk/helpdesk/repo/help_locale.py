"""SQL for help center languages (feature-gap plan 4.1): the workspace's default language,
article translations, translated category names, and the public reads that pick the best
translation. RLS applies to every statement."""

import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.helpdesk.domain.help_locale import DEFAULT_LOCALE

# Settings


async def lock_settings(
    session: AsyncSession, workspace_id: UUID, *, exclusive: bool
) -> RowMapping:
    """The workspace's settings (`default_locale`, `seo_indexable`), with the row locked until
    the end of the
    transaction. A translation write takes it shared and a change of default takes it
    exclusive, so the two can never both succeed for the same language. The row is made
    the first time it is needed."""
    await session.execute(
        text(
            "INSERT INTO hd_help_center_settings (workspace_id) VALUES (:ws) "
            "ON CONFLICT (workspace_id) DO NOTHING"
        ),
        {"ws": workspace_id},
    )
    lock = "FOR UPDATE" if exclusive else "FOR SHARE"
    result = await session.execute(
        text(
            "SELECT default_locale, seo_indexable FROM hd_help_center_settings "  # noqa: S608
            f"WHERE workspace_id = :ws {lock}"
        ),
        {"ws": workspace_id},
    )
    return result.mappings().one()


async def default_locale(session: AsyncSession) -> str:
    """No settings row yet means English."""
    result = await session.execute(text("SELECT default_locale FROM hd_help_center_settings"))
    found = result.scalar_one_or_none()
    return str(found) if found else DEFAULT_LOCALE


async def settings(session: AsyncSession) -> tuple[str, bool]:
    """`(default_locale, seo_indexable)`; no settings row yet means English, not indexable."""
    result = await session.execute(
        text("SELECT default_locale, seo_indexable FROM hd_help_center_settings")
    )
    found = result.first()
    return (str(found[0]), bool(found[1])) if found else (DEFAULT_LOCALE, False)


async def set_settings(
    session: AsyncSession, *, locale: str, indexable: bool, member_id: UUID
) -> None:
    await session.execute(
        text(
            "UPDATE hd_help_center_settings SET default_locale = :locale, "
            "seo_indexable = :indexable, updated_by = :by, updated_at = now()"
        ),
        {"locale": locale, "indexable": indexable, "by": member_id},
    )


async def count_in_locale(session: AsyncSession, locale: str) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_article_translation WHERE locale = :locale"),
        {"locale": locale},
    )
    return int(result.scalar_one())


# Translations

_TRANSLATION = (
    "SELECT t.id, t.article_id, t.locale, t.title, t.body, t.status, t.slug, t.meta_title, "
    "t.meta_description, t.canonical_url, t.published_at, "
    "t.created_at, t.updated_at, m.name AS updated_by_name "
    "FROM hd_article_translation t LEFT JOIN member m ON m.id = t.updated_by "
)


async def translations(session: AsyncSession, article_id: UUID) -> Sequence[RowMapping]:
    result = await session.execute(
        text(_TRANSLATION + "WHERE t.article_id = :id ORDER BY t.locale"), {"id": article_id}
    )
    return result.mappings().all()


async def translation(session: AsyncSession, article_id: UUID, locale: str) -> RowMapping | None:
    result = await session.execute(
        text(_TRANSLATION + "WHERE t.article_id = :id AND t.locale = :locale"),
        {"id": article_id, "locale": locale},
    )
    return result.mappings().first()


async def count_for_article(session: AsyncSession, article_id: UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM hd_article_translation WHERE article_id = :id"),
        {"id": article_id},
    )
    return int(result.scalar_one())


async def upsert_translation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    article_id: UUID,
    locale: str,
    title: str,
    body: str,
    status: str,
    member_id: UUID,
    slug: str | None = None,
    meta_title: str | None = None,
    meta_description: str | None = None,
    canonical_url: str | None = None,
) -> None:
    """`published_at` is the first time it went live; unpublishing keeps it."""
    await session.execute(
        text(
            "INSERT INTO hd_article_translation (workspace_id, article_id, locale, title, "
            "body, status, updated_by, slug, meta_title, meta_description, canonical_url, "
            "published_at) VALUES (:ws, :article, :locale, :title, :body, :status, :by, "
            ":slug, :meta_title, :meta_description, :canonical_url, "
            "CASE WHEN :status = 'published' THEN now() END) "
            "ON CONFLICT (workspace_id, article_id, locale) DO UPDATE SET "
            "title = EXCLUDED.title, body = EXCLUDED.body, status = EXCLUDED.status, "
            "slug = EXCLUDED.slug, meta_title = EXCLUDED.meta_title, "
            "meta_description = EXCLUDED.meta_description, "
            "canonical_url = EXCLUDED.canonical_url, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "published_at = CASE WHEN EXCLUDED.status = 'published' "
            "THEN coalesce(hd_article_translation.published_at, now()) "
            "ELSE hd_article_translation.published_at END"
        ),
        {
            "ws": workspace_id,
            "article": article_id,
            "locale": locale,
            "title": title,
            "body": body,
            "status": status,
            "by": member_id,
            "slug": slug,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "canonical_url": canonical_url,
        },
    )


async def delete_translation(session: AsyncSession, article_id: UUID, locale: str) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM hd_article_translation WHERE article_id = :id AND locale = :locale "
            "RETURNING id"
        ),
        {"id": article_id, "locale": locale},
    )
    return result.first() is not None


# Category names


async def category_names(session: AsyncSession, category_id: UUID) -> dict[str, str] | None:
    """Locked: the caller changes one language and writes the whole set back."""
    result = await session.execute(
        text("SELECT name_translations FROM hd_article_category WHERE id = :id FOR UPDATE"),
        {"id": category_id},
    )
    found = result.scalar_one_or_none()
    return dict(found) if found is not None else None


async def set_category_names(
    session: AsyncSession, category_id: UUID, names: dict[str, str]
) -> None:
    await session.execute(
        text(
            "UPDATE hd_article_category SET name_translations = CAST(:names AS jsonb) "
            "WHERE id = :id"
        ),
        {"id": category_id, "names": json.dumps(names)},
    )


# The public help center: published articles, in the best language available.
#
# `:want` holds the translations to look for, best first (see `domain.help_locale.wanted`);
# an empty list serves the article itself, in the default language. A translation counts
# only when it is published, and the article must be published as well.

_PUBLIC_FROM = (
    "FROM hd_article a LEFT JOIN hd_article_category c ON c.id = a.category_id "
    "LEFT JOIN LATERAL (SELECT x.* FROM hd_article_translation x "
    "WHERE x.article_id = a.id AND x.status = 'published' "
    "AND x.locale = ANY(CAST(:want AS text[])) "
    "ORDER BY array_position(CAST(:want AS text[]), x.locale) LIMIT 1) t ON true "
)
_PUBLIC_ROW = (
    "SELECT a.id, a.category_id, a.created_at, "
    "coalesce(CAST(c.name_translations ->> CAST(:l1 AS text) AS text), "
    "CAST(c.name_translations ->> CAST(:l2 AS text) AS text), c.name) AS category_name, "
    "coalesce(t.title, a.title) AS title, coalesce(t.updated_at, a.updated_at) AS updated_at, "
    "coalesce(t.locale, CAST(:default AS text)) AS locale, "
    "CASE WHEN t.id IS NULL THEN a.slug ELSE t.slug END AS slug "
)


def _params(want: Sequence[str], default: str) -> dict[str, object]:
    return {
        "want": list(want),
        "l1": want[0] if want else None,
        "l2": want[1] if len(want) > 1 else None,
        "default": default,
    }


async def public_categories(
    session: AsyncSession, want: Sequence[str], default: str
) -> Sequence[RowMapping]:
    """Categories that hold a published article, named in the best language."""
    result = await session.execute(
        text(
            "SELECT c.id, coalesce(CAST(c.name_translations ->> CAST(:l1 AS text) AS text), "
            "CAST(c.name_translations ->> CAST(:l2 AS text) AS text), c.name) AS name, "
            "c.position, (SELECT count(*) FROM hd_article a WHERE a.category_id = c.id "
            "AND a.status = 'published') AS article_count "
            "FROM hd_article_category c ORDER BY c.position, c.name"
        ),
        _params(want, default),
    )
    return result.mappings().all()


async def public_list(
    session: AsyncSession,
    want: Sequence[str],
    default: str,
    *,
    category_id: UUID | None,
    before: tuple[datetime, UUID] | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Newest first."""
    result = await session.execute(
        text(
            _PUBLIC_ROW + _PUBLIC_FROM + "WHERE a.status = 'published' "
            "AND (CAST(:category AS uuid) IS NULL OR a.category_id = :category) "
            "AND (CAST(:at AS timestamptz) IS NULL OR (a.created_at, a.id) < (:at, :id)) "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit"
        ),
        {
            **_params(want, default),
            "category": category_id,
            "at": before[0] if before else None,
            "id": before[1] if before else None,
            "limit": limit,
        },
    )
    return result.mappings().all()


async def public_search(
    session: AsyncSession,
    want: Sequence[str],
    default: str,
    query: str,
    *,
    category_id: UUID | None,
    limit: int,
) -> Sequence[RowMapping]:
    """Best match first, in the language served: the translation's text when there is a
    published one, the article's own text when there is not (a draft translation never
    hides an article)."""
    result = await session.execute(
        text(
            _PUBLIC_ROW + _PUBLIC_FROM + "WHERE a.status = 'published' "
            "AND coalesce(t.search, a.search) @@ to_tsquery('simple', cedesk_unaccent(:q)) "
            "AND (CAST(:category AS uuid) IS NULL OR a.category_id = :category) "
            "ORDER BY ts_rank(coalesce(t.search, a.search), "
            "to_tsquery('simple', cedesk_unaccent(:q))) DESC, "
            "a.created_at DESC LIMIT :limit"
        ),
        {**_params(want, default), "q": query, "category": category_id, "limit": limit},
    )
    return result.mappings().all()


async def public_article(
    session: AsyncSession, article_id: UUID, want: Sequence[str], default: str
) -> RowMapping | None:
    """One published article, with every language it is published in."""
    result = await session.execute(
        text(
            _PUBLIC_ROW + ", coalesce(t.body, a.body) AS body, "  # noqa: S608
            # SEO text belongs to the row shown: a translation never takes the article's
            # canonical address (that would point every language at one page).
            "CASE WHEN t.id IS NULL THEN a.meta_title ELSE t.meta_title END AS meta_title, "
            "CASE WHEN t.id IS NULL THEN a.meta_description ELSE t.meta_description END "
            "AS meta_description, "
            "CASE WHEN t.id IS NULL THEN a.canonical_url ELSE t.canonical_url END "
            "AS canonical_url, "
            "(SELECT coalesce(jsonb_agg(jsonb_build_object('locale', u.l, 'slug', u.slug) "
            "ORDER BY u.l), '[]'::jsonb) FROM ("
            "SELECT CAST(:default AS text) AS l, a.slug AS slug UNION ALL "
            "SELECT y.locale, y.slug FROM hd_article_translation y "
            "WHERE y.article_id = a.id AND y.status = 'published') u) AS alternates "
            + _PUBLIC_FROM
            + "WHERE a.id = :id AND a.status = 'published'"
        ),
        {**_params(want, default), "id": article_id},
    )
    return result.mappings().first()


async def sitemap(session: AsyncSession, default: str, *, limit: int) -> Sequence[RowMapping]:
    """Published articles, oldest first, each with its published translations: one query, so
    the number of languages costs nothing extra."""
    result = await session.execute(
        text(
            "SELECT a.id, a.slug, a.updated_at, CAST(:default AS text) AS locale, "
            "(SELECT coalesce(jsonb_agg(jsonb_build_object('locale', y.locale, "
            "'slug', y.slug, 'updated_at', y.updated_at) ORDER BY y.locale), '[]'::jsonb) "
            "FROM hd_article_translation y WHERE y.article_id = a.id "
            "AND y.status = 'published') AS translations "
            "FROM hd_article a WHERE a.status = 'published' "
            "ORDER BY a.created_at, a.id LIMIT :limit"
        ),
        {"default": default, "limit": limit},
    )
    return result.mappings().all()
