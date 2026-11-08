"""Cursor pagination (md 2B: every list, default 50, maximum 200).

A cursor is the position of the last row a page returned, opaque to the client. Lists
ordered by time use (created_at, id): the id breaks ties between rows created in the
same microsecond, so a page never skips or repeats a row.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class CursorError(ValueError):
    """The cursor was not made by us, or belongs to another list or order."""


def encode(payload: list[object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> list[object]:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursorError("This cursor is not valid.") from exc
    if not isinstance(value, list):
        raise CursorError("This cursor is not valid.")
    return value


def encode_time(kind: str, at: datetime, row_id: UUID) -> str:
    return encode([kind, at.isoformat(), str(row_id)])


def decode_time(cursor: str, kind: str) -> tuple[datetime, UUID]:
    """`kind` names the list ("message", "notification"), so a cursor from one list
    is refused by another."""
    value = decode(cursor)
    try:
        name, at, row_id = value
        if name != kind:
            raise CursorError("This cursor belongs to another list.")
        parsed = datetime.fromisoformat(str(at))
        if parsed.tzinfo is None:
            raise CursorError("This cursor is not valid.")
        return parsed, UUID(str(row_id))
    except CursorError:
        raise
    except (ValueError, TypeError) as exc:
        raise CursorError("This cursor is not valid.") from exc


def time_page[R: Mapping[Any, Any]](
    rows: Sequence[R], limit: int, kind: str
) -> tuple[Sequence[R], str | None]:
    """`rows` was fetched with `limit + 1`: the page, and the cursor after its last row
    (None when there is nothing more)."""
    page = rows[:limit]
    if len(rows) <= limit:
        return page, None
    last = page[-1]
    return page, encode_time(kind, last["created_at"], last["id"])
