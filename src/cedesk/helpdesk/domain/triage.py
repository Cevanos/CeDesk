"""Keyword triage (md I1-I3, 6.5): a ticket's topic, the customer's mood and their
language, from the words the customer wrote. Keyword rules only; CeDesk has no AI (D11).

- Topic: the topic whose keywords appear most often; a tie goes to the first in `TOPICS`;
  no keyword at all is "General question". The keyword lists are the Admin's to edit.
- Sentiment: negative words (and negated positive ones: "not happy") against positive
  ones. Mood words only: "broken" or "damaged" describe the product, not the customer.
  Any negative word wins a tie: "thanks, but it is still not working" is negative.
- Language: Hindi when much of the text is Devanagari, or when it has several common
  Hindi words written in Latin letters ("mera order kab aayega"); English for other
  Latin text; None for other scripts or no letters.

Words match whole words, also with a common English ending ("refund" matches "refunds",
"refunded"); a keyword of several words matches them in a row.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from cedesk.helpdesk.domain.text import words
from cedesk.helpdesk.domain.tickets import Sentiment

GENERAL = "General question"
REFUND = "Refund / return"
BILLING = "Billing"
DELIVERY = "Delivery"
APP = "App problem"
TOPICS: tuple[str, ...] = (REFUND, BILLING, DELIVERY, APP)
"""The topics with keywords, in tie order. `GENERAL` is the fallback and has none."""

MAX_KEYWORDS = 100  # per topic
KEYWORD_MAX = 60  # characters
KEYWORD_WORDS = 5

DEFAULT_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    REFUND: (
        "refund", "return", "money back", "replace", "replacement", "exchange",
        "cancel order", "paisa wapas", "paise wapas", "wapas", "रिफंड", "वापस",
    ),
    BILLING: (
        "bill", "billing", "invoice", "charge", "payment", "paid", "pay", "upi", "gst",
        "double charged", "subscription", "deducted", "बिल", "भुगतान",
    ),
    DELIVERY: (
        "delivery", "deliver", "shipping", "shipped", "courier", "tracking", "track",
        "where is my order", "parcel", "package", "dispatch", "not received", "late",
        "delay", "delayed", "डिलीवरी", "पार्सल",
    ),
    APP: (
        "app", "crash", "error", "bug", "login", "log in", "password", "otp",
        "not loading", "website", "screen", "ऐप",
    ),
}  # fmt: skip

_ENDINGS = ("s", "es", "d", "ed", "ing")

_NEGATIVE = frozenset({
    "angry", "annoyed", "awful", "bad", "cheated", "complaint", "disappointed",
    "disappointing", "disgusting", "fake", "fraud", "frustrated", "frustrating",
    "furious", "hate", "horrible", "irritated", "pathetic", "poor",
    "ridiculous", "rubbish", "scam", "terrible", "unacceptable", "unhappy", "upset",
    "useless", "waste", "worst", "bakwas", "bekar", "bekaar", "ghatiya", "pareshan",
    "gussa", "dhokha", "kharab", "faltu", "बकवास", "बेकार", "घटिया", "परेशान", "गुस्सा",
    "धोखा", "खराब", "ख़राब", "फालतू",
})  # fmt: skip
_NEGATIVE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("not", "working"), ("not", "received"), ("no", "response"), ("still", "waiting"),
    ("fed", "up"), ("never", "again"), ("नहीं", "मिला"),
)  # fmt: skip
_POSITIVE = frozenset({
    "thanks", "thank", "thankyou", "thx", "appreciate", "appreciated", "awesome",
    "excellent", "fantastic", "good", "great", "happy", "helpful", "love", "nice",
    "perfect", "satisfied", "wonderful", "amazing", "dhanyavaad", "dhanyawad",
    "shukriya", "badhiya", "धन्यवाद", "शुक्रिया", "बढ़िया",
})  # fmt: skip
_NEGATORS = frozenset({"not", "no", "never", "nahi", "nahin", "नहीं", "नही"})
_INTENSIFIERS = frozenset({"very", "so", "really", "too", "that", "at", "all"})

_HINGLISH = frozenset({
    "hai", "hain", "nahi", "nahin", "kya", "kyun", "kyu", "mera", "meri", "mere", "mujhe",
    "humko", "hume", "aap", "aapka", "aapne", "apna", "kab", "kaise", "kahan", "abhi",
    "tak", "hua", "hui", "raha", "rahi", "rahe", "gaya", "gayi", "karo", "kijiye",
    "kripya", "jaldi", "paisa", "paise", "wapas", "bhai", "yaar", "bhi", "lekin", "kuch",
    "bahut", "bohot", "hoga", "chahiye", "kar", "diya", "mila", "aayega",
})  # fmt: skip
HINGLISH_WORDS = 2  # distinct common Hindi words in Latin letters
DEVANAGARI_SHARE = 0.3

_APOSTROPHE_NOT = re.compile("n['\u2019]t\\b", re.IGNORECASE)  # n't, either apostrophe


class BadKeywords(ValueError):
    """The Admin's keyword lists break the rules."""


@dataclass(frozen=True)
class Triage:
    topic: str
    sentiment: Sentiment
    language: str | None


def _tokens(text: str) -> list[str]:
    return words(_APOSTROPHE_NOT.sub(" not", text))  # "isn't" → "is not"


def _same(word: str, keyword: str) -> bool:
    if word == keyword:
        return True
    return word.startswith(keyword) and word[len(keyword) :] in _ENDINGS


def _count(tokens: Sequence[str], phrase: Sequence[str]) -> int:
    size = len(phrase)
    return sum(
        1
        for i in range(len(tokens) - size + 1)
        if all(_same(tokens[i + k], phrase[k]) for k in range(size))
    )


def _negated(tokens: Sequence[str], i: int) -> bool:
    """ "not happy", "not very happy", "not at all happy"; not "no problem, thanks"."""
    j = i - 1
    while j >= 0 and tokens[j] in _INTENSIFIERS:
        j -= 1
    return j >= 0 and tokens[j] in _NEGATORS


def clean_keywords(raw: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """Every topic in `TOPICS` once, each with its keywords trimmed, lowercased and
    without repeats."""
    if set(raw) != set(TOPICS):
        raise BadKeywords(f"Give keywords for exactly these topics: {', '.join(TOPICS)}.")
    out: dict[str, list[str]] = {}
    for topic in TOPICS:
        seen: dict[str, None] = {}
        for keyword in raw[topic]:
            clean = " ".join(keyword.strip().lower().split())
            if not clean:
                continue
            if len(clean) > KEYWORD_MAX or len(words(clean)) > KEYWORD_WORDS:
                raise BadKeywords(
                    f"A keyword is at most {KEYWORD_MAX} characters and {KEYWORD_WORDS} words."
                )
            if not words(clean):
                raise BadKeywords(f"'{clean}' has no letters or digits.")
            seen[clean] = None
        if len(seen) > MAX_KEYWORDS:
            raise BadKeywords(f"At most {MAX_KEYWORDS} keywords per topic.")
        out[topic] = list(seen)
    return out


def topic(text: str, keywords: Mapping[str, Sequence[str]]) -> str:
    tokens = _tokens(text)
    best, best_hits = GENERAL, 0
    for name in TOPICS:
        hits = sum(_count(tokens, words(k)) for k in keywords.get(name, ()))
        if hits > best_hits:
            best, best_hits = name, hits
    return best


def sentiment(text: str) -> Sentiment:
    tokens = _tokens(text)
    negative = sum(_count(tokens, p) for p in _NEGATIVE_PHRASES)
    positive = 0
    for i, word in enumerate(tokens):
        negated = _negated(tokens, i)
        if word in _NEGATIVE:
            negative += 0 if negated else 1  # "not bad" is neither
        elif word in _POSITIVE:
            if negated:
                negative += 1  # "not happy"
            else:
                positive += 1
    if negative and negative >= positive:
        return Sentiment.negative
    return Sentiment.positive if positive else Sentiment.neutral


def language(text: str) -> str | None:
    devanagari = sum(1 for c in text if "ऀ" <= c <= "ॿ" and c.isalpha())
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if devanagari + latin == 0:
        return None
    if devanagari / (devanagari + latin) >= DEVANAGARI_SHARE:
        return "hi"
    if not latin:
        return None
    if len(set(_tokens(text)) & _HINGLISH) >= HINGLISH_WORDS:
        return "hi"
    return "en"


def triage(text: str, *, mood_text: str | None = None,
           keywords: Mapping[str, Sequence[str]] = DEFAULT_KEYWORDS) -> Triage:  # fmt: skip
    """`text`: everything the customer wrote (subject included), for topic and language.
    `mood_text`: their newest message, when the mood should be the current one."""
    return Triage(
        topic=topic(text, keywords),
        sentiment=sentiment(mood_text if mood_text is not None else text),
        language=language(text),
    )


SKILL_FOR_LANGUAGE: Mapping[str, str] = {"hi": "hindi"}
"""Routing (md W6, 6.2): a Hindi ticket goes first to agents with the skill "hindi"."""
