"""Attachment rules that need no database: file names, file types and storage keys."""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

NAME_MAX = 255
EXTENSION_MAX = 20
DEFAULT_MIME = "application/octet-stream"
PER_MESSAGE_MAX = 10
"""Files an agent sends with one reply or note."""
RECEIVED_MAX = 20
"""Files kept from one customer email; the rest are named in a system line."""
EMAIL_TOTAL_MAX = 18 * 1024 * 1024
"""Files on one emailed reply, together. Encoding adds a third, so this stays under the
25 MB most mail services accept."""
# type/subtype as RFC 6838 allows them, lower-case, no parameters.
_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")


class ScanStatus(StrEnum):
    not_scanned = "not_scanned"
    clean = "clean"
    infected = "infected"
    scan_failed = "scan_failed"  # the scanner could not check it
    erased = "erased"  # deleted by a privacy erase (md S11); the row stays


BLOCKED = frozenset({ScanStatus.infected, ScanStatus.scan_failed, ScanStatus.erased})
"""Never downloaded, sent or emailed."""


def ready(status: str, *, scanning: bool) -> bool:
    """Whether a file may be handed out: never when blocked, and with scanning on only
    once it was found clean."""
    if status in BLOCKED:
        return False
    return status == ScanStatus.clean or not scanning


@dataclass(frozen=True)
class ReceivedFile:
    """A file that came with a customer's message (an email attachment)."""

    name: str
    mime: str
    data: bytes


def clean_name(raw: str) -> str:
    """The file's own name, safe to store and show: folders dropped, control and
    direction-changing characters removed, at most 255 characters with the extension
    kept. "file" when nothing usable is left."""
    name = unicodedata.normalize("NFC", raw).replace("\\", "/").rsplit("/", 1)[-1]
    # Category C covers control characters and format ones such as the right-to-left
    # override (U+202E), which can make an .exe name read like a .pdf.
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C")).strip()
    if name in {"", ".", ".."}:
        return "file"
    if len(name) > NAME_MAX:
        stem, dot, extension = name.rpartition(".")
        if dot and stem and len(extension) <= EXTENSION_MAX:
            name = f"{stem[: NAME_MAX - len(extension) - 1]}.{extension}"
        else:
            name = name[:NAME_MAX]
    return name


def clean_mime(raw: str | None) -> str:
    """The declared type without its parameters; anything malformed is stored as
    application/octet-stream."""
    value = (raw or "").split(";", 1)[0].strip().lower()
    return value if _MIME.match(value) else DEFAULT_MIME


def object_key(workspace_id: UUID, ticket_id: UUID, attachment_id: UUID) -> str:
    """Ids only: a file name never reaches a path or a bucket key."""
    return f"{workspace_id}/{ticket_id}/{attachment_id}"
