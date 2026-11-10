"""Virus scanning (md T18) with ClamAV's daemon (clamd), spoken to over TCP.

clamd's INSTREAM command: send `zINSTREAM\\0`, then the file in chunks, each led by its
length as a 4-byte big-endian number, then a zero length. The answer, ended by `\\0`, is
`stream: OK`, `stream: <virus name> FOUND`, or `<reason> ERROR`.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Protocol

from cedesk.settings import ScanBackend, Settings

CHUNK = 64 * 1024
ANSWER_MAX = 4096
_FOUND = re.compile(r"^stream: (.{1,200}) FOUND$")


@dataclass(frozen=True)
class Verdict:
    virus: str | None  # None: clean

    @property
    def clean(self) -> bool:
        return self.virus is None


class ScanError(Exception):
    """`permanent`: this file can't be checked (too big for the scanner, ...). Otherwise the
    scanner is unreachable or busy, and the file is tried again later."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Scanner(Protocol):
    async def scan(self, data: bytes) -> Verdict: ...


def read_answer(raw: bytes) -> Verdict:
    """clamd's answer to INSTREAM."""
    text = raw.rstrip(b"\0").decode("utf-8", "replace").strip()
    if text == "stream: OK":
        return Verdict(None)
    found = _FOUND.match(text)
    if found:
        return Verdict(found.group(1))
    if text.endswith("ERROR"):
        # e.g. "INSTREAM size limit exceeded. ERROR": this file will never pass.
        raise ScanError(text[:200], permanent=True)
    raise ScanError(f"Unexpected answer from the virus scanner: {text[:100]!r}")


class ClamdScanner:
    def __init__(self, host: str, port: int, timeout: float, max_bytes: int) -> None:
        self.host, self.port, self.timeout, self.max_bytes = host, port, timeout, max_bytes

    async def scan(self, data: bytes) -> Verdict:
        if len(data) > self.max_bytes:
            mb = self.max_bytes // (1024 * 1024)
            raise ScanError(f"Larger than the virus scanner accepts ({mb} MB).", permanent=True)
        try:
            return await asyncio.wait_for(self._scan(data), self.timeout)
        except TimeoutError:
            raise ScanError("The virus scanner did not answer in time.") from None
        except OSError as exc:
            raise ScanError(f"The virus scanner can't be reached: {exc.strerror or exc}") from None

    async def _scan(self, data: bytes) -> Verdict:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            try:
                writer.write(b"zINSTREAM\0")
                for start in range(0, len(data), CHUNK):
                    chunk = data[start : start + CHUNK]
                    writer.write(len(chunk).to_bytes(4, "big") + chunk)
                    await writer.drain()
                writer.write((0).to_bytes(4, "big"))
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                # clamd stops reading once a file passes its size limit; its answer
                # (already sent) says so.
                pass
            answer = await reader.readuntil(b"\0")
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                raise ScanError("The virus scanner closed the connection.") from None
            answer = exc.partial
        except asyncio.LimitOverrunError:
            raise ScanError("The virus scanner's answer was too long.") from None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        return read_answer(answer[:ANSWER_MAX])


def make_scanner(settings: Settings) -> Scanner | None:
    if settings.scan_backend is ScanBackend.clamd:
        return ClamdScanner(
            settings.clamd_host,
            settings.clamd_port,
            settings.clamd_timeout_seconds,
            settings.clamd_max_bytes,
        )
    return None
