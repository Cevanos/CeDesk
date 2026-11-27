"""Turning service results into small JSON for tool answers."""

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

# Tool results reach a model's context; long message bodies are cut.
TEXT_MAX = 4000


def jsonable(value: Any) -> Any:
    """Plain JSON: dataclasses and rows become objects, ids and times strings."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return None  # never hashes or file bodies
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, Iterable):
        return [jsonable(v) for v in value]
    return str(value)


def pick(row: Mapping[Any, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {k: jsonable(row.get(k)) for k in keys if k in row}


def cut(text: str | None, limit: int = TEXT_MAX) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + " [...]"
