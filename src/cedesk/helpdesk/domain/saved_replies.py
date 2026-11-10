"""Saved replies (md W19): rules for shortcuts. No database here.

A shortcut is what the agent types after `/` in the reply box. It is stored lower-case
and without the slash: `/Refund` and `refund` are the same shortcut. Placeholders in the
body (`{{customer.first_name}}` ...) are filled when the reply is sent, like any reply.
"""

import re
from collections.abc import Collection

SHORTCUT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_SHORTCUT = 32
MAX_BODY = 10_000
MAX_PER_WORKSPACE = 1000


class ShortcutError(ValueError):
    pass


def normalise_shortcut(raw: str) -> str:
    """`/Refund ` -> `refund`. Raises `ShortcutError` when it can't be a shortcut."""
    shortcut = raw.strip().removeprefix("/").lower()
    if not SHORTCUT.match(shortcut):
        raise ShortcutError(
            "A shortcut is 1-32 letters, digits, - or _, starting with a letter or digit."
        )
    return shortcut


def free_shortcut(wanted: str, taken: Collection[str]) -> str:
    """The shortcut an imported reply gets: `wanted` if free, else `wanted1`, `wanted2` ...
    (md W19: "a shortcut clash on import adds a suffix"). Keeps within the length limit."""
    shortcut = normalise_shortcut(wanted)
    if shortcut not in taken:
        return shortcut
    n = 1
    while True:
        suffix = str(n)
        candidate = shortcut[: MAX_SHORTCUT - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1
