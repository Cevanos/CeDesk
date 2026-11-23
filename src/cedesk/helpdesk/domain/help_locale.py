"""Help center languages (feature-gap plan 4.1) and SEO text (4.3): rules that need no database.

The article row is the workspace's default-language version; other languages are translation
rows. A language is a code like `en`, `hi` or `pt-br`, stored lower-case.
"""

import re
from collections.abc import Mapping

from cedesk.helpdesk.domain.articles import CATEGORY_MAX

DEFAULT_LOCALE = "en"
MAX_TRANSLATIONS = 40  # per article
MAX_CATEGORY_NAMES = 40  # per category

_LOCALE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


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


def wanted(requested: str | None, default: str) -> list[str]:
    """The translations to look for, best first: the exact language, then its base
    (`pt-br`, then `pt`). The default language is the article itself, so it is never
    looked up. An unknown or empty request asks for nothing: the default is served."""
    if not requested:
        return []
    exact = clean_locale(requested)
    found = [exact] if exact == base_of(exact) else [exact, base_of(exact)]
    return [loc for loc in found if loc != default]


def clean_category_names(names: Mapping[str, str]) -> dict[str, str]:
    """`{locale: name}` with each key cleaned and each name 1 to 80 characters."""
    if len(names) > MAX_CATEGORY_NAMES:
        raise BadLocale(f"A category can have at most {MAX_CATEGORY_NAMES} translated names.")
    cleaned: dict[str, str] = {}
    for code, name in names.items():
        value = " ".join(name.split())
        if not 1 <= len(value) <= CATEGORY_MAX:
            raise BadLocale(f"A category name needs 1 to {CATEGORY_MAX} characters.")
        cleaned[clean_locale(code)] = value
    return cleaned
