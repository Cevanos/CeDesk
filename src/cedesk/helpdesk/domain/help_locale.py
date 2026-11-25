"""Help center languages (feature-gap plan 4.1) and SEO text (4.3): rules that need no database.

The article row is the workspace's default-language version; other languages are translation
rows. A language is a code like `en`, `hi` or `pt-br`, stored lower-case.
"""

import re
import unicodedata
from collections.abc import Mapping
from urllib.parse import urlsplit

from cedesk.helpdesk.domain.articles import CATEGORY_MAX, nfc

DEFAULT_LOCALE = "en"
MAX_TRANSLATIONS = 40  # per article
MAX_CATEGORY_NAMES = 40  # per category

# A language, then at most a script and a region ("zh-hant-tw", "sr-latn-rs", "pt-br").
_LOCALE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8}){0,2}$")


class BadLocale(ValueError):
    pass


def clean_locale(code: str) -> str:
    """`pt-BR` -> `pt-br`. The frontend writes the region in capitals for hreflang."""
    value = code.strip().lower()
    if not _LOCALE.match(value):
        raise BadLocale("Give the language as a code, e.g. en, hi or pt-br.")
    return value


def base_of(locale: str) -> str:
    return locale.split("-", 1)[0]


def parents_of(locale: str) -> list[str]:
    """`zh-hant-tw` -> `zh-hant`, `zh`: the broader languages to try when it is missing."""
    parts = locale.split("-")
    return ["-".join(parts[:n]) for n in range(len(parts) - 1, 0, -1)]


def wanted(requested: str | None, default: str) -> list[str]:
    """The translations to look for, best first: the exact language, then its base
    (`pt-br`, then `pt`). The default language is the article itself, so it is never
    looked up. An unknown or empty request asks for nothing: the default is served."""
    if not requested:
        return []
    exact = clean_locale(requested)
    found = [exact, *parents_of(exact)]
    return [loc for loc in found if loc != default]


def clean_category_names(names: Mapping[str, str]) -> dict[str, str]:
    """`{locale: name}` with each key cleaned and each name 1 to 80 characters."""
    if len(names) > MAX_CATEGORY_NAMES:
        raise BadLocale(f"A category can have at most {MAX_CATEGORY_NAMES} translated names.")
    cleaned: dict[str, str] = {}
    for code, name in names.items():
        value = " ".join(nfc(name).split())
        if not 1 <= len(value) <= CATEGORY_MAX:
            raise BadLocale(f"A category name needs 1 to {CATEGORY_MAX} characters.")
        cleaned[clean_locale(code)] = value
    return cleaned


# SEO text (feature-gap plan 4.3)

SLUG_MIN = 3
SLUG_MAX = 100
META_TITLE_MAX = 120
META_DESCRIPTION_MAX = 320
CANONICAL_MAX = 500

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class BadSeo(ValueError):
    pass


def suggest_slug(title: str) -> str | None:
    """A URL word from a title: accents dropped, ASCII letters and digits joined by hyphens.
    A title with fewer than 3 usable characters (for example Hindi) gives none, and the
    page's address is just its id."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        cut = slug[:SLUG_MAX]  # end on a whole word when that leaves a usable slug
        whole = cut.rsplit("-", 1)[0] if "-" in cut else cut
        slug = whole if len(whole) >= SLUG_MIN else cut.rstrip("-")
    return slug if len(slug) >= SLUG_MIN else None


def clean_slug(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    slug = value.strip().lower()
    if not SLUG_MIN <= len(slug) <= SLUG_MAX or not _SLUG.match(slug):
        raise BadSeo(
            f"A slug is {SLUG_MIN} to {SLUG_MAX} lower-case letters, digits and single hyphens."
        )
    return slug


def _plain(value: str | None, limit: int, what: str) -> str | None:
    """One line of text: line breaks and runs of spaces become one space; other control
    characters are refused."""
    if value is None:
        return None
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in value):
        raise BadSeo(f"The {what} can't contain control characters.")
    text = " ".join(nfc(value).split())
    if not text:
        return None
    if len(text) > limit:
        raise BadSeo(f"The {what} can be at most {limit} characters.")
    return text


def clean_meta_title(value: str | None) -> str | None:
    return _plain(value, META_TITLE_MAX, "meta title")


def clean_meta_description(value: str | None) -> str | None:
    return _plain(value, META_DESCRIPTION_MAX, "meta description")


def clean_canonical(value: str | None) -> str | None:
    """An https address with a host, no sign-in details and no fragment, or None."""
    if value is None or not value.strip():
        return None
    url = value.strip()
    problem = BadSeo("The canonical URL must be a full https:// address.")
    if len(url) > CANONICAL_MAX or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise problem
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:  # e.g. an unclosed [ in the host
        raise problem from None
    if parts.scheme != "https" or not host or parts.fragment:
        raise problem
    if parts.username is not None or parts.password is not None:
        raise problem
    return url


SEO_FIELDS = ("slug", "meta_title", "meta_description", "canonical_url")


def clean_seo(values: Mapping[str, str | None]) -> dict[str, str | None]:
    """All four SEO fields, cleaned; an empty one is None (not set)."""
    return {
        "slug": clean_slug(values.get("slug")),
        "meta_title": clean_meta_title(values.get("meta_title")),
        "meta_description": clean_meta_description(values.get("meta_description")),
        "canonical_url": clean_canonical(values.get("canonical_url")),
    }
