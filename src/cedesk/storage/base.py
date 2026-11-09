"""What every storage driver offers, whichever one the settings pick."""

import re
from typing import Protocol
from urllib.parse import quote

# Keys are built by CeDesk from ids, never from a file name: segments of letters, digits,
# `_`, `.` and `-` that start with a letter or digit, so `..` can never appear as a segment.
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")
KEY_MAX = 512


class ObjectMissing(LookupError):
    """No object is stored under this key."""


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes:
        """Raises `ObjectMissing` for a key that holds nothing."""
        ...

    async def delete(self, key: str) -> None:
        """Deleting a missing key is not an error."""
        ...

    async def download_url(self, key: str, *, name: str, content_type: str, expires_in: int) -> str:
        """A link that downloads the object as `name` without signing in, for `expires_in`
        seconds. Absolute for S3; a path under this API for local disk."""
        ...


def check_key(key: str) -> str:
    if len(key) > KEY_MAX or not _KEY.match(key):
        raise ValueError("Not a storage key CeDesk makes.")
    return key


def content_disposition(name: str) -> str:
    """`attachment`, so a browser saves the file rather than opening it in our origin, with
    the name in plain ASCII for old clients and in full UTF-8 (RFC 6266)."""
    fallback = re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
