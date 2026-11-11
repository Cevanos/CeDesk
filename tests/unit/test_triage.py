"""Keyword triage (md I1-I3): topic, mood and language from the customer's words."""

import pytest

from cedesk.helpdesk.domain import triage as rules
from cedesk.helpdesk.domain.tickets import Sentiment
from cedesk.helpdesk.domain.triage import DEFAULT_KEYWORDS, sentiment, topic, triage


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I want a refund, it arrived torn", "Refund / return"),
        ("Please RETURN my money", "Refund / return"),
        ("Refunded twice? I returned it", "Refund / return"),  # common English endings
        ("Where is my order? Tracking shows nothing", "Delivery"),
        ("I was charged twice on my UPI payment", "Billing"),
        ("The app keeps crashing when I log in", "App problem"),
        ("मेरा रिफंड कब आएगा", "Refund / return"),
        ("Do you sell gift cards?", "General question"),
        ("", "General question"),
        ("approve my application", "General question"),  # "app" is a word, not a prefix
    ],
)
def test_topic(text: str, expected: str) -> None:
    assert topic(text, DEFAULT_KEYWORDS) == expected


def test_most_hits_win_and_a_tie_goes_to_the_first_topic() -> None:
    assert topic("payment failed, bill wrong, invoice missing, want refund", DEFAULT_KEYWORDS) == (
        "Billing"
    )
    assert topic("refund for the late delivery", DEFAULT_KEYWORDS) == "Delivery"  # 1 v 2
    assert topic("refund my payment", DEFAULT_KEYWORDS) == "Refund / return"  # 1 v 1: first


def test_a_phrase_matches_its_words_in_a_row_only() -> None:
    kw = {t: [] for t in rules.TOPICS} | {"Delivery": ["where is my order"]}
    assert topic("Hi, where is my order?", kw) == "Delivery"
    assert topic("my order is where?", kw) == "General question"


@pytest.mark.parametrize(
    ("text", "mood"),
    [
        ("I am not happy, this is the worst service", Sentiment.negative),
        ("Thanks but it is still not working", Sentiment.negative),  # any negative wins a tie
        ("it isn't good", Sentiment.negative),
        ("it isn\u2019t good", Sentiment.negative),  # a curly apostrophe
        ("not very happy at all", Sentiment.negative),
        ("No problem, thanks", Sentiment.positive),  # "no" is not next to "thanks"
        ("not bad", Sentiment.neutral),
        ("The mixer arrived broken and damaged", Sentiment.neutral),  # the product, not mood
        ("Thanks, the refund came. Great service!", Sentiment.positive),
        ("बहुत खराब सर्विस", Sentiment.negative),
        ("dhanyavaad bhai", Sentiment.positive),
        ("Where is my order", Sentiment.neutral),
    ],
)
def test_sentiment(text: str, mood: Sentiment) -> None:
    assert sentiment(text) is mood


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("मेरा पैसा वापस करो", "hi"),
        ("order T-1001 का क्या हुआ", "hi"),  # mostly Devanagari
        ("mera order kab aayega bhai", "hi"),  # Hindi in Latin letters
        ("Where is my order? hai", "en"),  # one Hindi-looking word is not enough
        ("Where is my order?", "en"),
        ("எனது ஆர்டர் எங்கே", None),  # Tamil: not told yet
        ("12345 !!!", None),
        ("", None),
    ],
)
def test_language(text: str, language: str | None) -> None:
    assert rules.language(text) == language


def test_the_mood_can_come_from_the_newest_message_only() -> None:
    found = triage("refund please, worst service ever", mood_text="ok thanks")
    assert (found.topic, found.sentiment, found.language) == (
        "Refund / return", Sentiment.positive, "en")  # fmt: skip


def test_keyword_lists_are_cleaned_and_checked() -> None:
    raw = {t: [] for t in rules.TOPICS} | {"Billing": ["  GST  Bill ", "gst bill", "", "UPI"]}
    assert rules.clean_keywords(raw)["Billing"] == ["gst bill", "upi"]
    with pytest.raises(rules.BadKeywords, match="exactly these topics"):
        rules.clean_keywords({"Billing": ["upi"]})
    with pytest.raises(rules.BadKeywords, match="exactly these topics"):
        rules.clean_keywords(raw | {"Other": []})
    for bad in ("x" * 61, "one two three four five six", "!!!"):
        with pytest.raises(rules.BadKeywords):
            rules.clean_keywords(raw | {"Billing": [bad]})
    with pytest.raises(rules.BadKeywords, match="At most 100"):
        rules.clean_keywords(raw | {"Billing": [f"k{n}" for n in range(101)]})


def test_custom_keywords_change_the_topic() -> None:
    kw = {t: [] for t in rules.TOPICS} | {"App problem": ["white screen"]}
    assert topic("I see a white screen", kw) == "App problem"
    assert topic("refund please", kw) == "General question"
