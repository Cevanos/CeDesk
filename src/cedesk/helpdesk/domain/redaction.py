"""Card numbers out of customer messages before they are saved (md I5, 6.5).

A card number is 13-19 digits, written together or in groups split by single spaces or
dashes ("4111 1111 1111 1111", "4111-1111-1111-1111"), that passes the Luhn check. Only
ASCII digits count. Inside a longer run of digit groups (a card number followed by the
CVV, "4111 1111 1111 1111 123"), the longest Luhn-valid span of whole groups is removed.
A group of more than 19 digits is never a card number (an order id, a tracking number).

Removal cannot be undone: the number is never stored.
"""

import re

REPLACEMENT = "[card number removed]"
MIN_DIGITS = 13
MAX_DIGITS = 19

_RUN = re.compile(r"(?<![0-9])[0-9]+(?:[ -][0-9]+)*(?![0-9])")
_GROUP = re.compile(r"[0-9]+")


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card_spans(run: str) -> list[tuple[int, int]]:
    """(start, end) offsets inside `run` of the card numbers in it."""
    groups = [(m.start(), m.end(), m.group()) for m in _GROUP.finditer(run)]
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(groups):
        found = None
        digits = ""
        for j in range(i, len(groups)):  # at most 19 digits: a few groups at a time
            digits += groups[j][2]
            if len(digits) > MAX_DIGITS:
                break
            if len(digits) >= MIN_DIGITS and luhn(digits):
                found = j  # keep going: the longest valid span wins
        if found is None:
            i += 1
            continue
        spans.append((groups[i][0], groups[found][1]))
        i = found + 1
    return spans


def redact(text: str) -> tuple[str, int]:
    """The text with card numbers replaced, and how many were replaced."""
    out, numbers = redact_numbers(text)
    return out, len(numbers)


def redact_numbers(text: str) -> tuple[str, list[str]]:
    """The text with card numbers replaced, and the numbers (digits only) that were, so a
    caller can count one number written twice (subject and message) once."""
    numbers: list[str] = []

    def replace(match: re.Match[str]) -> str:
        run = match.group()
        spans = _card_spans(run)
        if not spans:
            return run
        out, at = [], 0
        for start, end in spans:
            numbers.append("".join(ch for ch in run[start:end] if ch.isdigit()))
            out += [run[at:start], REPLACEMENT]
            at = end
        out.append(run[at:])
        return "".join(out)

    return _RUN.sub(replace, text), numbers


def line(count: int) -> str:
    """The system line on the ticket."""
    noun = "card number" if count == 1 else "card numbers"
    return f"{count} {noun} removed from the customer's message"
