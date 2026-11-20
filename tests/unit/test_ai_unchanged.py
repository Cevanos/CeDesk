"""Cevanos sending a text back as it was (md section 16), no database: what the agent is
told instead of seeing the same text as if it were the result."""

import pytest

from cedesk.helpdesk.domain import ai


def test_unchanged_is_the_same_text_spaces_aside() -> None:
    assert ai.unchanged("hi  priya\n", " hi priya")
    assert not ai.unchanged("hi priya", "Hi Priya")  # a change of case is a change
    assert not ai.unchanged("tshirt", "t-shirt")


@pytest.mark.parametrize(
    ("source", "to", "note"),
    [
        ("en", "en", "This is already in English."),
        ("en", "en-GB", "This is already in English."),
        ("pt-BR", "pt", "This is already in Portuguese."),
        ("xx", "xx", "This is already in that language."),
        (None, "en", "Cevanos found nothing to translate in this text."),
    ],
)
def test_a_text_that_needed_no_translation_gets_a_note(
    source: str | None, to: str, note: str
) -> None:
    assert ai.untranslated("Where is my order?", "Where is my order? ", source, to) == note


@pytest.mark.parametrize("text", ["ORD-1042", "Priya", "#1042 ✓", "3.1.7", "OK!"])
def test_a_code_or_a_single_word_needs_no_translation(text: str) -> None:
    """Live retest: "ORD-1042" to Hindi came back as it was, with from: en (rightly)."""
    assert ai.untranslated(text, text, "en", "hi") == ai.NOTHING_TO_TRANSLATE


def test_another_language_sent_back_untranslated_is_refused() -> None:
    """Live retest: "mera paisa wapas karo" came back as it was, with from: hi."""
    with pytest.raises(ai.BadAnswer, match="Cevanos didn't translate this"):
        ai.untranslated("mera paisa wapas karo", "mera paisa wapas karo", "hi", "en")


def test_a_real_translation_gets_no_note() -> None:
    assert ai.untranslated("Mera order kahan hai?", "Where is my order?", "hi", "en") is None
