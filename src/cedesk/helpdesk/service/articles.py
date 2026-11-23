"""Help center (md P6): categories, articles, search, and "Was this helpful?" votes.

Agents (anyone who can read tickets) read every article, drafts too, to help customers;
writing is a Settings action (Admin), as the permissions table puts Settings. The public
help center shows only published articles, no sign-in. Search is Postgres full text, no
AI (md D11). Votes are one per visitor per article (changeable) and counted for agents;
the article report itself is Phase 3 (R8).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import articles as rules
from cedesk.helpdesk.domain import audit as audit_rules
from cedesk.helpdesk.domain import help_locale, markdown, paging
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import articles as repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import help_locale as locale_repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

ARTICLE_FIELDS = ("title", "status", "category_id", "body")

NO_ARTICLE = "Article not found."
NO_CATEGORY = "Category not found."
NO_HELP_CENTER = "Help center not found."
NAME_TAKEN = "A category with that name already exists."
CURSOR_KIND = "article"


@dataclass(frozen=True)
class ArticlePage:
    articles: Sequence[RowMapping]
    next_cursor: str | None  # None for a search (best matches only)


def _checked(clean: Callable[[str], str], value: str) -> str:
    try:
        return clean(value)
    except rules.BadArticle as exc:
        raise Invalid(str(exc)) from exc


def _clean_locale(code: str) -> str:
    try:
        return help_locale.clean_locale(code)
    except help_locale.BadLocale as exc:
        raise Invalid(str(exc)) from exc


def _wanted(requested: str | None, default: str) -> list[str]:
    try:
        return help_locale.wanted(requested, default)
    except help_locale.BadLocale as exc:
        raise Invalid(str(exc)) from exc


def _before(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        return paging.decode_time(cursor, CURSOR_KIND)
    except paging.CursorError as exc:
        raise Invalid(str(exc)) from exc


async def _page(
    s: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    category_id: UUID | None,
    cursor: str | None,
    limit: int,
    missing_locale: str | None = None,
) -> ArticlePage:
    if q is not None and q.strip():
        query = rules.search_query(q)
        if query is None:
            return ArticlePage([], None)
        found = await repo.search(
            s, query, status=status, category_id=category_id, limit=rules.SEARCH_RESULTS,
            missing_locale=missing_locale,
        )  # fmt: skip
        return ArticlePage(found, None)
    rows = await repo.list_articles(
        s, status=status, category_id=category_id, before=_before(cursor), limit=limit + 1,
        missing_locale=missing_locale,
    )  # fmt: skip
    page, next_cursor = paging.time_page(rows, limit, CURSOR_KIND)
    return ArticlePage(page, next_cursor)


async def _known_category(s: AsyncSession, category_id: UUID | None) -> None:
    if category_id is not None and await repo.category(s, category_id) is None:
        raise Invalid("Unknown category.")


# Agents


async def list_categories(sm: Sessionmaker, caller: Caller) -> Sequence[RowMapping]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.categories(s, published_only=False)


async def create_category(sm: Sessionmaker, caller: Caller, name: str, position: int) -> UUID:
    require(caller, Action.settings)
    clean = _checked(rules.clean_category, name)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            category_id = await repo.insert_category(s, caller.workspace_id, clean, position)
            await audit.record(
                s, caller, Area.help_center, "created", f"Category {clean}", target_id=category_id
            )
            return category_id
    except IntegrityError as exc:
        raise Conflict(NAME_TAKEN) from exc


async def update_category(
    sm: Sessionmaker,
    caller: Caller,
    category_id: UUID,
    *,
    name: str | None = None,
    position: int | None = None,
) -> None:
    require(caller, Action.settings)
    try:
        async with workspace_session(sm, caller.workspace_id) as s:
            current = await repo.category(s, category_id)
            if current is None:
                raise NotFound(NO_CATEGORY)
            clean = _checked(rules.clean_category, name) if name is not None else current["name"]
            where = position if position is not None else current["position"]
            await repo.update_category(s, category_id, clean, where)
            changed = audit_rules.changes(
                current, {"name": clean, "position": where}, fields=("name", "position")
            )
            if changed:
                await audit.record(
                    s,
                    caller,
                    Area.help_center,
                    "changed",
                    f"Category {clean}",
                    target_id=category_id,
                    detail={"changes": changed},
                )
    except IntegrityError as exc:
        raise Conflict(NAME_TAKEN) from exc


async def delete_category(sm: Sessionmaker, caller: Caller, category_id: UUID) -> None:
    """Its articles stay, with no category."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.category(s, category_id)
        if current is None or not await repo.delete_category(s, category_id):
            raise NotFound(NO_CATEGORY)
        await audit.record(
            s,
            caller,
            Area.help_center,
            "deleted",
            f"Category {current['name']}",
            target_id=category_id,
        )


async def list_articles(
    sm: Sessionmaker,
    caller: Caller,
    *,
    q: str | None = None,
    status: rules.ArticleStatus | None = None,
    category_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
    missing_locale: str | None = None,
) -> ArticlePage:
    require(caller, Action.read_ticket)
    missing = _clean_locale(missing_locale) if missing_locale else None
    async with workspace_session(sm, caller.workspace_id) as s:
        return await _page(
            s,
            q=q,
            status=status.value if status else None,
            category_id=category_id,
            cursor=cursor,
            limit=limit,
            missing_locale=missing,
        )


async def get_article(sm: Sessionmaker, caller: Caller, article_id: UUID) -> RowMapping:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        found = await repo.article(s, article_id)
    if found is None:
        raise NotFound(NO_ARTICLE)
    return found


async def create_article(
    sm: Sessionmaker,
    caller: Caller,
    *,
    title: str,
    body: str,
    status: rules.ArticleStatus,
    category_id: UUID | None,
) -> RowMapping:
    require(caller, Action.settings)
    clean_title = _checked(rules.clean_title, title)
    clean_body = _checked(rules.clean_body, body)
    async with workspace_session(sm, caller.workspace_id) as s:
        await _known_category(s, category_id)
        article_id = await repo.insert_article(
            s,
            workspace_id=caller.workspace_id,
            category_id=category_id,
            title=clean_title,
            body=clean_body,
            status=status.value,
            member_id=caller.member_id,
        )
        found = await repo.article(s, article_id)
        await audit.record(
            s,
            caller,
            Area.help_center,
            "created",
            f"Article {clean_title}",
            target_id=article_id,
            detail=audit_rules.snapshot(found, ARTICLE_FIELDS[1:]),
        )
    assert found is not None
    return found


async def update_article(
    sm: Sessionmaker, caller: Caller, article_id: UUID, changes: Mapping[str, Any]
) -> RowMapping:
    """Only the keys present change: title, body, status, category_id (None: no category)."""
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.lock_article(s, article_id)
        if current is None:
            raise NotFound(NO_ARTICLE)
        category_id = changes.get("category_id", current["category_id"])
        if "category_id" in changes:
            await _known_category(s, category_id)
        status = changes.get("status", current["status"])
        await repo.update_article(
            s,
            article_id,
            category_id=category_id,
            title=_checked(rules.clean_title, changes.get("title", current["title"])),
            body=_checked(rules.clean_body, changes.get("body", current["body"])),
            status=str(getattr(status, "value", status)),
            member_id=caller.member_id,
        )
        found = await repo.article(s, article_id)
        assert found is not None
        changed = audit_rules.changes(current, found, fields=ARTICLE_FIELDS)
        if changed:
            await audit.record(
                s,
                caller,
                Area.help_center,
                "changed",
                f"Article {found['title']}",
                target_id=article_id,
                detail={"changes": changed},
            )
    return found


async def delete_article(sm: Sessionmaker, caller: Caller, article_id: UUID) -> None:
    require(caller, Action.settings)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.lock_article(s, article_id)
        if current is None or not await repo.delete_article(s, article_id):
            raise NotFound(NO_ARTICLE)
        await audit.record(
            s,
            caller,
            Area.help_center,
            "deleted",
            f"Article {current['title']}",
            target_id=article_id,
            detail=audit_rules.snapshot(current, ARTICLE_FIELDS[1:]),
        )


async def markdown_everywhere(sm: Sessionmaker) -> int:
    """`cedesk articles-markdown`, once: imported articles still holding their source's
    HTML (Zendesk and Intercom imports kept it until now) are written as Markdown, so the
    help center shows them formatted instead of showing their tags. Safe to run again: a
    converted body has no tags left. Returns how many articles changed."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    changed = 0
    for workspace_id in workspace_ids:
        async with workspace_session(sm, workspace_id) as s:
            for row in await repo.imported_with_tags(s):
                body = str(row["body"])
                if markdown.has_html(body):
                    await repo.set_body(s, row["id"], markdown.from_html(body)[: rules.BODY_MAX])
                    changed += 1
    return changed


# The public help center (no sign-in; published articles only)


async def _workspace(sm: Sessionmaker, slug: str) -> UUID:
    async with sm() as s, s.begin():
        workspace_id = await auth_repo.workspace_for_slug(s, slug.strip().lower())
    if workspace_id is None:
        raise NotFound(NO_HELP_CENTER)
    return workspace_id


@dataclass(frozen=True)
class HelpCenter:
    workspace_name: str
    default_locale: str
    locale: str  # the language asked for (the default one when none was)
    categories: Sequence[RowMapping]  # with published article counts
    page: ArticlePage


async def _public_page(
    s: AsyncSession,
    want: Sequence[str],
    default: str,
    *,
    q: str | None,
    category_id: UUID | None,
    cursor: str | None,
    limit: int,
) -> ArticlePage:
    """The published articles in the best language: the same paging and search as the
    agents' list, but each article is shown in its translation when there is one."""
    if q is not None and q.strip():
        query = rules.search_query(q)
        if query is None:
            return ArticlePage([], None)
        found = await locale_repo.public_search(
            s, want, default, query, category_id=category_id, limit=rules.SEARCH_RESULTS
        )
        return ArticlePage(found, None)
    rows = await locale_repo.public_list(
        s, want, default, category_id=category_id, before=_before(cursor), limit=limit + 1
    )
    page, next_cursor = paging.time_page(rows, limit, CURSOR_KIND)
    return ArticlePage(page, next_cursor)


async def help_center(
    sm: Sessionmaker,
    slug: str,
    *,
    q: str | None = None,
    category_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = paging.DEFAULT_LIMIT,
    locale: str | None = None,
) -> HelpCenter:
    workspace_id = await _workspace(sm, slug)
    async with workspace_session(sm, workspace_id) as s:
        workspace = await auth_repo.workspace_row(s)
        default = await locale_repo.default_locale(s)
        want = _wanted(locale, default)
        categories = await locale_repo.public_categories(s, want, default)
        page = await _public_page(
            s, want, default, q=q, category_id=category_id, cursor=cursor, limit=limit
        )
    name = str(workspace["name"]) if workspace else ""
    asked = _clean_locale(locale) if locale else default
    return HelpCenter(name, default, asked, [c for c in categories if c["article_count"]], page)


@dataclass(frozen=True)
class PublicArticle:
    row: RowMapping
    default_locale: str
    fallback: bool  # a language was asked for and the article is shown in another


async def public_article(
    sm: Sessionmaker, slug: str, article_id: UUID, *, locale: str | None = None
) -> PublicArticle:
    workspace_id = await _workspace(sm, slug)
    async with workspace_session(sm, workspace_id) as s:
        default = await locale_repo.default_locale(s)
        found = await locale_repo.public_article(s, article_id, _wanted(locale, default), default)
    if found is None:
        raise NotFound(NO_ARTICLE)
    fallback = bool(locale) and found["locale"] != _clean_locale(str(locale))
    return PublicArticle(found, default, fallback)


async def vote(
    sm: Sessionmaker,
    slug: str,
    article_id: UUID,
    *,
    helpful: bool,
    visitor: str,
    locale: str | None = None,
) -> None:
    """One vote per visitor per article; voting again changes it. `locale` is the language
    of the page the visitor voted on."""
    workspace_id = await _workspace(sm, slug)
    page_locale = _clean_locale(locale) if locale else None
    async with workspace_session(sm, workspace_id) as s:
        if await repo.article(s, article_id, published_only=True) is None:
            raise NotFound(NO_ARTICLE)
        await repo.vote(
            s,
            workspace_id=workspace_id,
            article_id=article_id,
            visitor_hash=rules.visitor_hash(visitor),
            helpful=helpful,
            locale=page_locale,
        )
