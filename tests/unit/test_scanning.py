"""The clamd client against a fake clamd on a local port (no ClamAV needed)."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from cedesk.scanning import ClamdScanner, ScanError, read_answer

Reply = Callable[[bytes], bytes]


async def fake_clamd(reply: Reply) -> tuple[asyncio.Server, int, list[bytes]]:
    """Reads one INSTREAM, answers `reply(file)`."""
    got: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while size := int.from_bytes(await reader.readexactly(4), "big"):
            data += await reader.readexactly(size)
        got.append(data)
        writer.write(reply(data))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], got


@pytest.fixture
async def clamd() -> AsyncIterator[Callable[[Reply], Awaitable[tuple[ClamdScanner, list[bytes]]]]]:
    servers: list[asyncio.Server] = []

    async def start(reply: Reply) -> tuple[ClamdScanner, list[bytes]]:
        server, port, got = await fake_clamd(reply)
        servers.append(server)
        return ClamdScanner("127.0.0.1", port, timeout=5, max_bytes=10_000_000), got

    yield start
    for server in servers:
        server.close()


async def test_clean_and_infected(clamd: Any) -> None:
    def reply(data: bytes) -> bytes:
        return b"stream: Eicar-Test-Signature FOUND\0" if b"EICAR" in data else b"stream: OK\0"

    scanner, got = await clamd(reply)
    big = b"x" * 200_000  # several chunks
    assert (await scanner.scan(big)).clean
    assert got == [big]
    assert (await scanner.scan(b"..EICAR..")).virus == "Eicar-Test-Signature"


async def test_a_file_too_big_for_clamd_is_permanent(clamd: Any) -> None:
    scanner, _ = await clamd(lambda _: b"INSTREAM size limit exceeded. ERROR\0")
    with pytest.raises(ScanError) as err:
        await scanner.scan(b"data")
    assert err.value.permanent


async def test_no_clamd_is_temporary() -> None:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(ScanError) as err:
        await ClamdScanner("127.0.0.1", port, timeout=5, max_bytes=10_000_000).scan(b"data")
    assert not err.value.permanent


async def test_a_file_over_the_limit_is_not_sent(clamd: Any) -> None:
    scanner, got = await clamd(lambda _: b"stream: OK\0")
    scanner.max_bytes = 1024
    with pytest.raises(ScanError) as err:
        await scanner.scan(b"x" * 1025)
    assert err.value.permanent and "1024" not in str(err.value)
    assert got == []  # never sent to clamd
    assert (await scanner.scan(b"x" * 1024)).clean


def test_answers() -> None:
    assert read_answer(b"stream: OK\0").clean
    assert read_answer(b"stream: Win.Test.EICAR_HDB-1 FOUND\0").virus == "Win.Test.EICAR_HDB-1"
    with pytest.raises(ScanError) as err:
        read_answer(b"something odd")
    assert not err.value.permanent


# The EICAR test file: harmless, and reported by every scanner. Built from two halves so
# this source file itself is not flagged.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.mark.skipif(
    not os.environ.get("CEDESK_TEST_CLAMD_HOST"),
    reason="set CEDESK_TEST_CLAMD_HOST (and _PORT) to check a real ClamAV",
)
async def test_a_real_clamav_finds_eicar_and_passes_a_normal_file() -> None:
    """Run after switching scanning on: `CEDESK_TEST_CLAMD_HOST=127.0.0.1 uv run pytest
    tests/unit/test_scanning.py -k real` with clamd's port reachable."""
    scanner = ClamdScanner(
        os.environ["CEDESK_TEST_CLAMD_HOST"],
        int(os.environ.get("CEDESK_TEST_CLAMD_PORT", "3310")),
        timeout=30,
        max_bytes=100 * 1024 * 1024,
    )
    infected = await scanner.scan(EICAR)
    assert not infected.clean and "eicar" in (infected.virus or "").lower()
    assert (await scanner.scan(b"%PDF-1.4 an ordinary invoice")).clean
