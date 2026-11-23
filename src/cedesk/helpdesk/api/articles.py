"""Help center for agents (md P6): categories and articles. Anyone who can read tickets
reads them (drafts too); writing is Admin (Settings). The public side is in `public.py`.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain.articles import ArticleStatus
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.service import articles

router = APIRouter(tags=["help center"])

Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ArticleBody = Annotated[str, StringConstraints(max_length=100_000)]
CategoryName = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class CategoryOut(BaseModel):
    id: UUID
    name: str
    name_translations: dict[str, str] = Field(
        description="The name in other languages, `{language: name}`."
    )
    position: int
    source: str | None = Field(description="Set on imported categories, e.g. zendesk.")
    article_count: int
    created_at: datetime


class CategoryIn(Body):
    name: CategoryName
    position: int = Field(default=0, ge=0, le=10_000, description="Lower shows first.")


class CategoryPatch(Body):
    name: CategoryName | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)


class ArticleOut(BaseModel):
    id: UUID
    title: str
    body: str = Field(description="Markdown. Render it without raw HTML.")
    status: ArticleStatus
    category_id: UUID | None
    category_name: str | None
    source: str | None
    published_at: datetime | None = Field(description="When it first went live.")
    created_at: datetime
    updated_at: datetime
    updated_by_name: str | None
    helpful_count: int = Field(description='"Was this helpful?" yes votes.')
    not_helpful_count: int
    locales: list[str] = Field(description="The languages it is translated into (any status).")


class ArticlesOut(BaseModel):
    articles: list[ArticleOut] = Field(description="Newest first; with `q`, best match first.")
    next_cursor: str | None = Field(description="null with `q` (the best 20 only).")


class ArticleIn(Body):
    title: Title
    body: ArticleBody = ""
    status: ArticleStatus = ArticleStatus.draft
    category_id: UUID | None = None


class ArticlePatch(Body):
    title: Title | None = None
    body: ArticleBody | None = None
    status: ArticleStatus | None = None
    category_id: UUID | None = Field(default=None, description="null: no category.")


_ADMIN: dict[int | str, dict[str, Any]] = {403: {"description": "Admins only"}}


@router.get("/article-categories", response_model=list[CategoryOut], summary="Categories")
async def list_categories(caller: CallerDep, sm: SessionmakerDep) -> list[CategoryOut]:
    rows = await articles.list_categories(sm, caller)
    return [CategoryOut.model_validate(dict(r)) for r in rows]


@router.post(
    "/article-categories",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a category (Admin)",
    responses={**_ADMIN, 409: {"description": "That name is taken"}},
)
async def create_category(body: CategoryIn, caller: CallerDep, sm: SessionmakerDep) -> CategoryOut:
    new_id = await articles.create_category(sm, caller, body.name, body.position)
    return await _category(sm, caller, new_id)


async def _category(sm: SessionmakerDep, caller: CallerDep, category_id: UUID) -> CategoryOut:
    rows = await articles.list_categories(sm, caller)
    return CategoryOut.model_validate(dict(next(r for r in rows if r["id"] == category_id)))


@router.patch(
    "/article-categories/{category_id}",
    response_model=CategoryOut,
    summary="Rename or move a category (Admin)",
    responses={**_ADMIN, 404: {"description": "Not found"}, 409: {"description": "Name taken"}},
)
async def update_category(
    category_id: UUID, body: CategoryPatch, caller: CallerDep, sm: SessionmakerDep
) -> CategoryOut:
    await articles.update_category(sm, caller, category_id, name=body.name, position=body.position)
    return await _category(sm, caller, category_id)


@router.delete(
    "/article-categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a category; its articles stay, with no category (Admin)",
    responses={**_ADMIN, 404: {"description": "Not found"}},
)
async def delete_category(category_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await articles.delete_category(sm, caller, category_id)


@router.get("/articles", response_model=ArticlesOut, summary="Articles, drafts included")
async def list_articles(
    caller: CallerDep,
    sm: SessionmakerDep,
    q: Annotated[str | None, Query(max_length=200, description="Search words")] = None,
    status_filter: Annotated[ArticleStatus | None, Query(alias="status")] = None,
    category_id: UUID | None = None,
    missing_locale: Annotated[
        str | None,
        Query(max_length=11, description="Only articles with no translation in this language."),
    ] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> ArticlesOut:
    page = await articles.list_articles(
        sm, caller, q=q, status=status_filter, category_id=category_id, cursor=cursor,
        limit=limit, missing_locale=missing_locale,
    )  # fmt: skip
    return ArticlesOut(
        articles=[ArticleOut.model_validate(dict(r)) for r in page.articles],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/articles",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Write an article (Admin)",
    responses={**_ADMIN, 422: {"description": "Unknown category, or too long"}},
)
async def create_article(body: ArticleIn, caller: CallerDep, sm: SessionmakerDep) -> ArticleOut:
    row = await articles.create_article(
        sm, caller, title=body.title, body=body.body, status=body.status,
        category_id=body.category_id,
    )  # fmt: skip
    return ArticleOut.model_validate(dict(row))


@router.get(
    "/articles/{article_id}",
    response_model=ArticleOut,
    summary="One article",
    responses={404: {"description": "Not found"}},
)
async def get_article(article_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> ArticleOut:
    return ArticleOut.model_validate(dict(await articles.get_article(sm, caller, article_id)))


@router.patch(
    "/articles/{article_id}",
    response_model=ArticleOut,
    summary="Edit, publish or unpublish an article (Admin)",
    description="Send only what changes. `status: published` puts it in the public help "
    "center; `draft` takes it out.",
    responses={**_ADMIN, 404: {"description": "Not found"}, 422: {"description": "Invalid"}},
)
async def update_article(
    article_id: UUID, body: ArticlePatch, caller: CallerDep, sm: SessionmakerDep
) -> ArticleOut:
    changes = body.model_dump(exclude_unset=True)
    for key in ("title", "body", "status"):
        if key in changes and changes[key] is None:
            del changes[key]  # null means "no change" for these; only category clears
    row = await articles.update_article(sm, caller, article_id, changes)
    return ArticleOut.model_validate(dict(row))


@router.delete(
    "/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an article and its votes (Admin)",
    responses={**_ADMIN, 404: {"description": "Not found"}},
)
async def delete_article(article_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await articles.delete_article(sm, caller, article_id)
