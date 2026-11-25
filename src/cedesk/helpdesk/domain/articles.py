"""Help center rules (md P6) that need no database."""

import hashlib
import unicodedata
from enum import StrEnum

from cedesk.helpdesk.domain.text import words

TITLE_MAX = 200
BODY_MAX = 100_000
CATEGORY_MAX = 80
SEARCH_WORDS = 8
SEARCH_RESULTS = 20


class ArticleStatus(StrEnum):
    draft = "draft"
    published = "published"


class BadArticle(ValueError):
    pass


def nfc(value: str) -> str:
    """Text typed or pasted in the decomposed form (some Mac apps: "e" + a combining accent)
    is stored composed, so one word is always one spelling in search and in comparisons."""
    return unicodedata.normalize("NFC", value)


def clean_title(title: str) -> str:
    value = " ".join(nfc(title).split())
    if not 1 <= len(value) <= TITLE_MAX:
        raise BadArticle(f"A title needs 1 to {TITLE_MAX} characters.")
    return value


def clean_body(body: str) -> str:
    value = nfc(body).strip()
    if len(value) > BODY_MAX:
        raise BadArticle(f"An article can be at most {BODY_MAX} characters.")
    return value


def clean_category(name: str) -> str:
    value = " ".join(nfc(name).split())
    if not 1 <= len(value) <= CATEGORY_MAX:
        raise BadArticle(f"A category name needs 1 to {CATEGORY_MAX} characters.")
    return value


def search_query(text: str) -> str | None:
    """A Postgres tsquery from what the customer typed: each word must match, the last
    one as a prefix ("refu" finds "refund"). Only letters, marks and digits get through,
    so the result is always valid tsquery syntax. None: nothing to search for."""
    found = words(nfc(text))[:SEARCH_WORDS]
    if not found:
        return None
    return " & ".join([*found[:-1], found[-1] + ":*"])


def visitor_hash(visitor: str) -> bytes:
    """The browser's random visitor id is stored only as a hash."""
    return hashlib.sha256(f"article-vote:{visitor}".encode()).digest()
