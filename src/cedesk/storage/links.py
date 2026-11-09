"""Signed download links for files kept on local disk.

A link carries everything the download needs (object key, file name, type, expiry) plus an
HMAC-SHA256 over it, so serving the file reads no database, and a changed or expired link
is refused. S3 signs its own links and never uses this.
"""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass


class BadLink(ValueError):
    """Not a link CeDesk signed, or one that was changed."""


class ExpiredLink(BadLink):
    """A genuine link whose time is up."""


@dataclass(frozen=True)
class FileLink:
    key: str
    name: str
    content_type: str
    expires_at: int
    """Unix seconds."""


def link_key(secret: str) -> bytes:
    """A key used for file links only, derived from the app secret, so a link signature can
    never stand in for any other signature the app makes."""
    return hmac.new(secret.encode(), b"cedesk/file-links/v1", hashlib.sha256).digest()


def sign(link: FileLink, key: bytes) -> str:
    payload = {"k": link.key, "n": link.name, "t": link.content_type, "e": link.expires_at}
    body = _encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_encode(_mac(key, body))}"


def verify(token: str, key: bytes, *, now: int) -> FileLink:
    body, dot, mac = token.partition(".")
    if not dot or not hmac.compare_digest(_encode(_mac(key, body)).encode(), mac.encode()):
        raise BadLink("This download link is not valid.")
    try:
        data = json.loads(_decode(body))
        link = FileLink(
            key=str(data["k"]),
            name=str(data["n"]),
            content_type=str(data["t"]),
            expires_at=int(data["e"]),
        )
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise BadLink("This download link is not valid.") from exc
    if now >= link.expires_at:
        raise ExpiredLink("This download link has expired. Ask for a new one.")
    return link


def _mac(key: bytes, body: str) -> bytes:
    return hmac.new(key, body.encode(), hashlib.sha256).digest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
