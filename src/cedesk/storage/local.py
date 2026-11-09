"""Files in a folder on this server: the simplest self-hosted setup.

Downloads go through `GET /api/v1/files/{token}`, a link signed with the app secret
(`links.py`). Back the folder up with the database, or use the S3 driver.
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path

from cedesk.storage import links
from cedesk.storage.base import ObjectMissing, check_key

FILES_PATH = "/api/v1/files"


class LocalStore:
    def __init__(self, root: Path, link_key: bytes) -> None:
        # Nothing touches the disk until the first upload, so an app that never stores a
        # file never needs the folder.
        self.root = root
        self.link_key = link_key

    def path(self, key: str) -> Path:
        """Where `key` lives. Refuses any key that would land outside the folder."""
        root = self.root.resolve()
        path = (root / check_key(key)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Not a storage key CeDesk makes.")
        return path

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        path = self.path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written beside its final name, then renamed: nobody ever reads half a file.
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self.path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise ObjectMissing(key) from None

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.path(key).unlink, missing_ok=True)

    async def download_url(self, key: str, *, name: str, content_type: str, expires_in: int) -> str:
        link = links.FileLink(
            key=check_key(key),
            name=name,
            content_type=content_type,
            expires_at=int(time.time()) + expires_in,
        )
        return f"{FILES_PATH}/{links.sign(link, self.link_key)}"
