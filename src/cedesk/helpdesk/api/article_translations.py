"""Help center languages for agents (feature-gap plan 4.1): the default language, article
translations and translated category names. Anyone who can read tickets reads them (drafts
too); writing is Admin (Settings). The public side is in `public.py`.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, status
from pydantic import BaseModel, Field, StringConstraints, computed_field

from cedesk.helpdesk.api.articles import SeoIn
from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import help_locale
from cedesk.helpdesk.domain.articles import ArticleStatus
from cedesk.helpdesk.service import article_translations as service

router = APIRouter(tags=["help center"])

LocaleCode = Annotated[
    str,
    Path(
        max_length=11,
        description="A language code such as `hi` or `pt-br` (stored lower-case).",
    ),
]
Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]
TranslatedBody = Annotated[str, StringConstraints(max_length=100_000)]
CategoryName = Annotated[str, StringConstraints(min_length=1, max_length=80)]

_ADMIN: dict[int | str, dict[str, Any]] = {403: {"description": "Admins only"}}


class HelpCenterSettingsOut(BaseModel):
    default_locale: str = Field(
        description="The language the articles themselves are written in. Every other "
        "language is a translation."
    )
    seo_indexable: bool = Field(
        description="Whether search engines may index the public help center. Off until an "
        "Admin turns it on; the frontend serves `noindex` and an empty sitemap meanwhile."
    )


class HelpCenterSettingsIn(Body):
    default_locale: Annotated[str, StringConstraints(min_length=2, max_length=11)] | None = None
    seo_indexable: bool | None = Field(default=None, description="Send only what changes.")


class TranslationOut(BaseModel):
    locale: str
    title: str
    body: str = Field(description="Markdown. Render it without raw HTML.")
    status: ArticleStatus = Field(description="Published only counts when the article is too.")
    slug: str | None
    meta_title: str | None
    meta_description: str | None
    canonical_url: str | None = Field(
        description="This language's own override; null means it is its own canonical page."
    )
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    updated_by_name: str | None

    @computed_field(description="A slug made from the title, or null when it gives none.")  # type: ignore[prop-decorator]
    @property
    def suggested_slug(self) -> str | None:
        return help_locale.suggest_slug(self.title)


class TranslationIn(SeoIn):
    title: Title
    body: TranslatedBody = ""
    status: ArticleStatus = ArticleStatus.draft


class CategoryNamesOut(BaseModel):
    name_translations: dict[str, str] = Field(description="`{language: name}`.")


class CategoryNameIn(Body):
    name: CategoryName


@router.get(
    "/help-center/settings",
    response_model=HelpCenterSettingsOut,
    summary="The help center's default language",
)
async def get_settings(caller: CallerDep, sm: SessionmakerDep) -> HelpCenterSettingsOut:
    found = await service.get_settings(sm, caller)
    return HelpCenterSettingsOut(
        default_locale=found.default_locale, seo_indexable=found.seo_indexable
    )


@router.put(
    "/help-center/settings",
    response_model=HelpCenterSettingsOut,
    summary="Change the default language or the indexable switch (Admin)",
    description="Send only what changes. A new default language is refused (409) while an "
    "article has a translation in it.",
    responses={**_ADMIN, 409: {"description": "Translations exist in that language"},
               422: {"description": "Not a language code"}},
)  # fmt: skip
async def put_settings(
    body: HelpCenterSettingsIn, caller: CallerDep, sm: SessionmakerDep
) -> HelpCenterSettingsOut:
    found = await service.update_settings(
        sm, caller, default_locale=body.default_locale, seo_indexable=body.seo_indexable
    )
    return HelpCenterSettingsOut(
        default_locale=found.default_locale, seo_indexable=found.seo_indexable
    )


@router.get(
    "/articles/{article_id}/translations",
    response_model=list[TranslationOut],
    summary="An article's translations, drafts included",
    responses={404: {"description": "Not found"}},
)
async def list_translations(
    article_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> list[TranslationOut]:
    rows = await service.list_translations(sm, caller, article_id)
    return [TranslationOut.model_validate(dict(r)) for r in rows]


@router.put(
    "/articles/{article_id}/translations/{locale}",
    response_model=TranslationOut,
    summary="Write an article's translation in one language (Admin)",
    description="Makes it or replaces it. It shows in the public help center when both it "
    "and the article are published. Up to 40 languages per article.",
    responses={**_ADMIN, 404: {"description": "Not found"},
               422: {"description": "Default language, bad code, too long or too many"}},
)  # fmt: skip
async def put_translation(
    article_id: UUID, locale: LocaleCode, body: TranslationIn, caller: CallerDep,
    sm: SessionmakerDep,
) -> TranslationOut:  # fmt: skip
    row = await service.save_translation(
        sm,
        caller,
        article_id,
        locale,
        title=body.title,
        body=body.body,
        status=body.status,
        seo=body.model_dump(include=set(help_locale.SEO_FIELDS)),
    )
    return TranslationOut.model_validate(dict(row))


@router.delete(
    "/articles/{article_id}/translations/{locale}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one translation (Admin)",
    responses={**_ADMIN, 404: {"description": "No such article or translation"}},
)
async def delete_translation(
    article_id: UUID, locale: LocaleCode, caller: CallerDep, sm: SessionmakerDep
) -> None:
    await service.delete_translation(sm, caller, article_id, locale)


@router.put(
    "/article-categories/{category_id}/translations/{locale}",
    response_model=CategoryNamesOut,
    summary="A category's name in one language (Admin)",
    responses={**_ADMIN, 404: {"description": "Not found"}, 422: {"description": "Invalid"}},
)
async def put_category_name(
    category_id: UUID, locale: LocaleCode, body: CategoryNameIn, caller: CallerDep,
    sm: SessionmakerDep,
) -> CategoryNamesOut:  # fmt: skip
    names = await service.save_category_name(sm, caller, category_id, locale, body.name)
    return CategoryNamesOut(name_translations=names)


@router.delete(
    "/article-categories/{category_id}/translations/{locale}",
    response_model=CategoryNamesOut,
    summary="Remove a category's name in one language (Admin)",
    responses={**_ADMIN, 404: {"description": "No such category or name"}},
)
async def delete_category_name(
    category_id: UUID, locale: LocaleCode, caller: CallerDep, sm: SessionmakerDep
) -> CategoryNamesOut:
    names = await service.delete_category_name(sm, caller, category_id, locale)
    return CategoryNamesOut(name_translations=names)
