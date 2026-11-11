"""Splitting customer text into words, for search and triage, in any script."""

import unicodedata


def words(text: str) -> list[str]:
    """Letters, combining marks and digits make words, lowercased. Python's `\\w` leaves
    out marks, which would split Hindi ("रिफंड") into single letters."""
    keep = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text)
    return [w.lower() for w in keep.split()]
