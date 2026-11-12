"""Audit log rules (md S9): what an entry holds, how entries are chained, and how a
change is described.

Each entry's hash is sha256(previous hash + its fields as canonical JSON), starting from
32 zero bytes. Verifying re-hashes every entry in order: an edited, removed or inserted
row breaks the chain from that point.
"""

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID

GENESIS = bytes(32)
MAX_VALUE = 300  # characters kept of one changed value
HIDDEN = "(hidden)"
# A field whose name ends in one of these words is a secret: the log shows only that it
# changed. Settings about secrets (password_min, api_key_hint) end otherwise and are shown.
_SECRET_ENDINGS = frozenset({"password", "secret", "token", "key", "hash", "cert", "sealed"})


class Area(StrEnum):
    """Filter on the audit page. Other teams' features add their own (api_keys,
    webhooks, apps, retention): the column takes any lower-case name."""

    members = "members"
    roles = "roles"
    security = "security"
    sso = "sso"
    two_factor = "two_factor"
    teams = "teams"
    ticket_types = "ticket_types"
    routing = "routing"
    sla = "sla"
    rules = "rules"
    macros = "macros"
    saved_replies = "saved_replies"
    email = "email"
    portal = "portal"
    help_center = "help_center"
    triage = "triage"
    cevanos = "cevanos"
    sandbox = "sandbox"
    privacy = "privacy"
    imports = "imports"


@dataclass(frozen=True)
class Entry:
    seq: int
    at: datetime
    actor_id: UUID | None
    actor_name: str
    ip: str | None
    area: str
    action: str
    target: str
    target_id: UUID | None
    detail: Mapping[str, Any]

    def fields(self) -> dict[str, Any]:
        """What the hash covers, in a form that reads back the same from the database."""
        return {
            "seq": self.seq,
            "at": self.at.astimezone(UTC).isoformat(timespec="microseconds"),
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_name": self.actor_name,
            "ip": self.ip,
            "area": self.area,
            "action": self.action,
            "target": self.target,
            "target_id": str(self.target_id) if self.target_id else None,
            "detail": json_safe(self.detail),
        }


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(prev_hash: bytes, entry: Entry) -> bytes:
    return hashlib.sha256(prev_hash + canonical(entry.fields())).digest()


def _plain(value: Any) -> Any:
    """For json.dumps: dataclasses as objects, sets sorted, enums by value, times as ISO."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda v: json.dumps(v, default=_plain, sort_keys=True))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def json_safe(value: Any) -> Any:
    """Plain JSON values only (as jsonb gives them back), in a fixed order, so a diff and
    the hash are stable."""
    return json.loads(json.dumps(value, default=_plain, sort_keys=True))


def _is_secret(field: str) -> bool:
    return field.lower().rsplit("_", 1)[-1] in _SECRET_ENDINGS


def _short(value: Any) -> Any:
    value = json_safe(value)
    if isinstance(value, str) and len(value) > MAX_VALUE:
        return value[: MAX_VALUE - 1] + "…"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(text) > MAX_VALUE:
            return text[: MAX_VALUE - 1] + "…"
    return value


def changes(
    before: Mapping[Any, Any] | None,
    after: Mapping[Any, Any] | None,
    *,
    fields: Iterable[str] | None = None,
) -> dict[str, list[Any]]:
    """{field: [before, after]} for the fields that differ (all shared fields when
    `fields` is None). Secret fields show only that they changed; long values are cut."""
    before = before or {}
    after = after or {}
    names = list(fields) if fields is not None else sorted(set(before) | set(after))
    out: dict[str, list[Any]] = {}
    for name in names:
        old, new = before.get(name), after.get(name)
        if json_safe(old) == json_safe(new):
            continue
        out[name] = [HIDDEN, HIDDEN] if _is_secret(name) else [_short(old), _short(new)]
    return out


def snapshot(row: Mapping[Any, Any] | None, fields: Iterable[str]) -> dict[str, Any]:
    """The listed fields of a row, secrets hidden: what a created or deleted thing was."""
    if row is None:
        return {}
    return {f: HIDDEN if _is_secret(f) else _short(row.get(f)) for f in fields if f in row}


@dataclass(frozen=True)
class Verified:
    ok: bool
    checked: int
    broken_at: int | None  # the first entry whose hash doesn't match, or a gap in seq


def verify(rows: Iterable[tuple[Entry, bytes, bytes]]) -> Verified:
    """`rows`: (entry, prev_hash, hash) in seq order, from the first entry."""
    expected_prev = GENESIS
    checked = 0
    for entry, prev_hash, stored in rows:
        if entry.seq != checked + 1 or prev_hash != expected_prev:
            return Verified(False, checked, entry.seq)
        if entry_hash(prev_hash, entry) != stored:
            return Verified(False, checked, entry.seq)
        expected_prev = stored
        checked += 1
    return Verified(True, checked, None)
