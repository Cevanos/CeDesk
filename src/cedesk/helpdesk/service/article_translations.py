"""Help center languages (feature-gap plan 4.1): the workspace's default language, article
translations and translated category names.

Anyone who can read tickets reads them (drafts too); writing is a Settings action (Admin),
like articles and categories. The public side, which picks the best translation, is in
`service/articles.py`.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import articles as rules
from cedesk.helpdesk.domain import help_locale
from cedesk.helpdesk.domain.audit import Area
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.repo import articles as article_repo
from cedesk.helpdesk.repo import help_locale as repo
from cedesk.helpdesk.service import audit
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Conflict, Invalid, NotFound
from cedesk.helpdesk.service.members import require

NO_ARTICLE = "Article not found."
NO_CATEGORY = "Category not found."
NO_TRANSLATION = "That article has no translation in that language."
IS_DEFAULT = "That is the default language: edit the article itself."
TOO_MANY = f"An article can have at most {help_locale.MAX_TRANSLATIONS} translations."


def _locale(code: str) -> str:
    try:
        return help_locale.clean_locale(code)
    except help_locale.BadLocale as exc:
        raise Invalid(str(exc)) from exc


# The default language


async def default_locale(sm: Sessionmaker, caller: Caller) -> str:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return await repo.default_locale(s)


async def set_default_locale(sm: Sessionmaker, caller: Caller, code: str) -> str:
    """Changes which language the article rows are in. Refused while any article has a
    translation in the new default: the article would have two versions in one language
    (delete those translations first). The settings row is locked while this runs, so a
    translation being saved in that language at the same moment cannot slip through."""
    require(caller, Action.settings)
    locale = _locale(code)
    async with workspace_session(sm, caller.workspace_id) as s:
        current = await repo.lock_settings(s, caller.workspace_id, exclusive=True)
        if locale == current:
            return current
        clashing = await repo.count_in_locale(s, locale)
        if clashing:
            raise Conflict(
                f"{clashing} article translation(s) are in '{locale}'. Delete them "
                "(or translate in another language) before making it the default."
            )
        await repo.set_default_locale(s, locale, caller.member_id)
        await audit.record(
            s, caller, Area.help_center, "changed", "Help center default language",
            detail={"changes": {"default_locale": {"from": current, "to": locale}}},
        )  # fmt: skip
    return locale


# Translations


async def list_translations(
    sm: Sessionmaker, caller: Caller, article_id: UUID
) -> Sequence[RowMapping]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await article_repo.article(s, article_id) is None:
            raise NotFound(NO_ARTICLE)
        return await repo.translations(s, article_id)


async def save_translation(
    sm: Sessionmaker,
    caller: Caller,
    article_id: UUID,
    code: str,
    *,
    title: str,
    body: str,
    status: rules.ArticleStatus,
) -> RowMapping:
    """Makes or replaces the translation in one language."""
    require(caller, Action.settings)
    locale = _locale(code)
    try:
        clean_title = rules.clean_title(title)
        clean_body = rules.clean_body(body)
    except rules.BadArticle as exc:
        raise Invalid(str(exc)) from exc
    async with workspace_session(sm, caller.workspace_id) as s:
        default = await repo.lock_settings(s, caller.workspace_id, exclusive=False)
        if locale == default:
            raise Invalid(IS_DEFAULT)
        if await article_repo.lock_article(s, article_id) is None:
            raise NotFound(NO_ARTICLE)
        existing = await repo.translation(s, article_id, locale)
        if existing is None and await repo.count_for_article(s, article_id) >= (
            help_locale.MAX_TRANSLATIONS
        ):
            raise Invalid(TOO_MANY)
        await repo.upsert_translation(
            s, workspace_id=caller.workspace_id, article_id=article_id, locale=locale,
            title=clean_title, body=clean_body, status=status.value, member_id=caller.member_id,
        )  # fmt: skip
        found = await repo.translation(s, article_id, locale)
        assert found is not None
        await audit.record(
            s, caller, Area.help_center, "changed" if existing else "created",
            f"Translation {locale} of article {clean_title}", target_id=article_id,
            detail={"locale": locale, "status": status.value},
        )  # fmt: skip
    return found


async def delete_translation(sm: Sessionmaker, caller: Caller, article_id: UUID, code: str) -> None:
    require(caller, Action.settings)
    locale = _locale(code)
    async with workspace_session(sm, caller.workspace_id) as s:
        if await article_repo.lock_article(s, article_id) is None:
            raise NotFound(NO_ARTICLE)
        if not await repo.delete_translation(s, article_id, locale):
            raise NotFound(NO_TRANSLATION)
        await audit.record(
            s, caller, Area.help_center, "deleted", f"Translation {locale}",
            target_id=article_id, detail={"locale": locale},
        )  # fmt: skip


# Category names


async def save_category_name(
    sm: Sessionmaker, caller: Caller, category_id: UUID, code: str, name: str
) -> dict[str, str]:
    """The category's name in one language; the rest are kept."""
    require(caller, Action.settings)
    locale = _locale(code)
    async with workspace_session(sm, caller.workspace_id) as s:
        default = await repo.lock_settings(s, caller.workspace_id, exclusive=False)
        if locale == default:
            raise Invalid("That is the default language: rename the category itself.")
        names = await repo.category_names(s, category_id)
        if names is None:
            raise NotFound(NO_CATEGORY)
        try:
            names = help_locale.clean_category_names({**names, locale: name})
        except help_locale.BadLocale as exc:
            raise Invalid(str(exc)) from exc
        await repo.set_category_names(s, category_id, names)
        await audit.record(
            s, caller, Area.help_center, "changed", f"Category name in {locale}",
            target_id=category_id, detail={"locale": locale},
        )  # fmt: skip
    return names


async def delete_category_name(
    sm: Sessionmaker, caller: Caller, category_id: UUID, code: str
) -> dict[str, str]:
    require(caller, Action.settings)
    locale = _locale(code)
    async with workspace_session(sm, caller.workspace_id) as s:
        names = await repo.category_names(s, category_id)
        if names is None:
            raise NotFound(NO_CATEGORY)
        if locale not in names:
            raise NotFound(NO_TRANSLATION)
        del names[locale]
        await repo.set_category_names(s, category_id, names)
        await audit.record(
            s, caller, Area.help_center, "changed", f"Category name in {locale} removed",
            target_id=category_id, detail={"locale": locale},
        )  # fmt: skip
    return names
