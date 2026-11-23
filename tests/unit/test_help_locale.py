"""Help center language rules (feature-gap plan 4.1) that need no database."""

import pytest

from cedesk.helpdesk.domain import help_locale as h


def test_a_language_code_is_cleaned_to_lower_case() -> None:
    assert h.clean_locale(" PT-BR ") == "pt-br"
    assert h.clean_locale("hi") == "hi"
    assert h.clean_locale("fil") == "fil"


@pytest.mark.parametrize(
    "bad", ["", "e", "english", "en_US", "en-", "-en", "en-x", "en us", "12", "en-" + "a" * 9]
)
def test_things_that_are_not_language_codes_are_refused(bad: str) -> None:
    with pytest.raises(h.BadLocale):
        h.clean_locale(bad)


def test_the_best_translation_is_the_exact_language_then_its_base() -> None:
    assert h.wanted("pt-br", "en") == ["pt-br", "pt"]
    assert h.wanted("PT-BR", "en") == ["pt-br", "pt"]
    assert h.wanted("hi", "en") == ["hi"]


def test_the_default_language_is_never_looked_up() -> None:
    assert h.wanted("en", "en") == []
    assert h.wanted("en-gb", "en") == ["en-gb"]  # the base is the default: the article itself
    assert h.wanted("pt-br", "pt") == ["pt-br"]
    assert h.wanted(None, "en") == []
    assert h.wanted("", "en") == []


def test_asking_for_a_bad_code_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(h.BadLocale):
        h.wanted("english", "en")


def test_category_names_are_cleaned_and_limited() -> None:
    assert h.clean_category_names({"HI": "  खाते   और बिल "}) == {"hi": "खाते और बिल"}
    with pytest.raises(h.BadLocale):
        h.clean_category_names({"hi": " "})
    with pytest.raises(h.BadLocale):
        h.clean_category_names({"hi": "x" * 81})
    with pytest.raises(h.BadLocale):
        h.clean_category_names({"bad code": "x"})
    many = {f"a{chr(97 + i // 26)}{chr(97 + i % 26)}": "n" for i in range(h.MAX_CATEGORY_NAMES + 1)}
    with pytest.raises(h.BadLocale):
        h.clean_category_names(many)
