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


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Refund policy", "refund-policy"),
        ("Réinitialiser le mot de passe", "reinitialiser-le-mot-de-passe"),
        ("  --Hello,  World!! ", "hello-world"),
        ("2024 update: what's new?", "2024-update-what-s-new"),
        ("धनवापसी नीति", None),  # nothing usable: the address is just the id
        ("Hi", None),
        ("日本語 FAQ", "faq"),
    ],
)
def test_a_slug_is_suggested_from_a_title(title: str, slug: str | None) -> None:
    assert h.suggest_slug(title) == slug


def test_a_long_title_gives_a_slug_that_fits_and_ends_on_a_word() -> None:
    slug = h.suggest_slug("word " * 60)
    assert slug is not None and len(slug) <= h.SLUG_MAX
    assert not slug.endswith("-") and slug.startswith("word-word")
    assert h.suggest_slug("x" * 300) == "x" * h.SLUG_MAX


def test_a_cut_slug_keeps_a_short_first_word_rather_than_nothing() -> None:
    slug = h.suggest_slug("ab " + "x" * 120)
    assert slug is not None and slug.startswith("ab-x") and len(slug) == h.SLUG_MAX
    assert h.clean_slug(slug) == slug  # and it is a valid slug


def test_a_slug_is_checked_and_lower_cased() -> None:
    assert h.clean_slug(" Refund-Policy ") == "refund-policy"
    assert h.clean_slug(None) is None
    assert h.clean_slug("  ") is None
    for bad in ("ab", "x" * 101, "has space", "under_score", "double--hyphen", "-lead", "trail-"):
        with pytest.raises(h.BadSeo):
            h.clean_slug(bad)


def test_meta_text_is_one_line_of_plain_text() -> None:
    assert h.clean_meta_title("  Refund \n\n policy\t") == "Refund policy"
    assert h.clean_meta_title("   ") is None
    assert h.clean_meta_title(None) is None
    assert h.clean_meta_description("a" * h.META_DESCRIPTION_MAX) is not None
    with pytest.raises(h.BadSeo):
        h.clean_meta_title("x" * (h.META_TITLE_MAX + 1))
    with pytest.raises(h.BadSeo):
        h.clean_meta_description("x" * (h.META_DESCRIPTION_MAX + 1))
    with pytest.raises(h.BadSeo):
        h.clean_meta_title("bad\x00title")
    with pytest.raises(h.BadSeo):
        h.clean_meta_description("esc\x1b[31m")


def test_a_canonical_url_is_a_plain_https_address() -> None:
    assert h.clean_canonical(" https://example.com/help/refunds?x=1 ") == (
        "https://example.com/help/refunds?x=1"
    )
    assert h.clean_canonical("") is None
    assert h.clean_canonical(None) is None
    bad = (
        "http://example.com/x", "//example.com/x", "https://", "https:///path",
        "https://user:pw@example.com/", "https://user@example.com/", "https://example.com/#top",
        "https://exa mple.com/", "https://example.com/a\nb", "javascript:alert(1)",
        "https://[::1", "https://example.com/" + "a" * 500, "example.com",
    )  # fmt: skip
    for url in bad:
        with pytest.raises(h.BadSeo):
            h.clean_canonical(url)


def test_all_four_seo_fields_are_cleaned_together() -> None:
    cleaned = h.clean_seo({"slug": "Hello-World", "meta_title": " T ", "canonical_url": ""})
    assert cleaned == {
        "slug": "hello-world", "meta_title": "T", "meta_description": None, "canonical_url": None
    }  # fmt: skip
    assert h.clean_seo({}) == dict.fromkeys(h.SEO_FIELDS)


def test_a_language_may_have_a_script_and_a_region() -> None:
    assert h.clean_locale("ZH-Hant-TW") == "zh-hant-tw"
    assert h.clean_locale("sr-latn-rs") == "sr-latn-rs"
    for bad in ("en-us-x-y", "zh-hant-tw-x", "zh--tw"):
        with pytest.raises(h.BadLocale):
            h.clean_locale(bad)


def test_a_three_part_language_falls_back_one_step_at_a_time() -> None:
    assert h.parents_of("zh-hant-tw") == ["zh-hant", "zh"]
    assert h.parents_of("pt-br") == ["pt"]
    assert h.parents_of("hi") == []
    assert h.wanted("zh-Hant-TW", "en") == ["zh-hant-tw", "zh-hant", "zh"]


def test_text_in_the_decomposed_form_is_stored_composed() -> None:
    import unicodedata

    from cedesk.helpdesk.domain import articles as a

    decomposed = unicodedata.normalize("NFD", "Réinitialiser le mot de passe")
    assert decomposed != "Réinitialiser le mot de passe"
    assert a.clean_title(decomposed) == "Réinitialiser le mot de passe"
    assert a.clean_body(decomposed + "  ") == "Réinitialiser le mot de passe"
    assert a.clean_category(decomposed) == "Réinitialiser le mot de passe"
    assert a.search_query(decomposed) == "réinitialiser & le & mot & de & passe:*"
