"""Virus scan (md T18) with RLS on, a fake scanner and the real API with scanning switched
on: a file is handed out only once found clean; an infected file's bytes are removed and
the ticket says why; a scanner that is down leaves files waiting, and emails with them.

The test database is shared: the scan pass sees other tests' files too. `OurFiles` lets
those through as clean, so only this workspace's files are really checked."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.helpdesk.service.email import send_due
from cedesk.helpdesk.service.scanning import scan_due
from cedesk.mail import OutgoingEmail
from cedesk.main import create_app
from cedesk.scanning import ScanError, Verdict
from cedesk.settings import Settings
from cedesk.storage import ObjectMissing, ObjectStore
from tests.db.helpers import Sm, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.fixture
async def app_parts(app_sessionmaker: Sm, tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Any]]:
    settings = Settings(sign_in_limit=100_000, storage_local_path=str(tmp_path / "files"),
                        scan_backend="clamd", clamd_host="clamd.test")  # fmt: skip
    app = create_app(settings)
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c, app.state.store


@pytest.fixture
def client(app_parts: tuple[AsyncClient, Any]) -> AsyncClient:
    return app_parts[0]


class OurFiles:
    """The real store for this workspace's files; other tests' files read as empty."""

    def __init__(self, store: ObjectStore, ws: UUID) -> None:
        self.store, self.prefix = store, f"{ws}/"

    async def get(self, key: str) -> bytes:
        if not key.startswith(self.prefix):
            return b""
        return await self.store.get(key)

    async def delete(self, key: str) -> None:
        await self.store.delete(key)


class FakeScanner:
    def __init__(self) -> None:
        self.down = False
        self.cannot: bytes | None = None  # files with these bytes are too big for it

    async def scan(self, data: bytes) -> Verdict:
        if self.down:
            raise ScanError("The virus scanner can't be reached: refused")
        if self.cannot is not None and self.cannot in data:
            raise ScanError("INSTREAM size limit exceeded. ERROR", permanent=True)
        return Verdict("Eicar-Test-Signature" if b"EICAR" in data else None)


class Catch:
    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        if email.from_address.endswith("@" + self.domain):
            self.sent.append(email)


async def desk(app_parts: tuple[AsyncClient, Any], sm: Sm, channel: str = "web") -> dict[str, Any]:
    client, store = app_parts
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    admin, ws = bearer(token), UUID(token.split(".")[0])
    domain = f"{slug}.test"
    ok(await client.post("/api/v1/email-addresses", headers=admin,
                         json={"address": f"support@{domain}", "name": "Acme"}), 201)  # fmt: skip
    ticket = ok(await client.post(T, headers=admin, json={
        "subject": "Broken parcel", "channel": channel,
        "contact": {"email": "ana@shop.test", "name": "Ana"}}), 201)  # fmt: skip
    return {"admin": admin, "ws": ws, "domain": domain, "url": f"{T}/{ticket['id']}",
            "store": cast(ObjectStore, OurFiles(store, ws)), "raw_store": store}  # fmt: skip


async def upload(client: AsyncClient, w: dict[str, Any], name: str, data: bytes) -> str:
    r = await client.post(f"{w['url']}/attachments", params={"name": name}, content=data,
                          headers={**w["admin"], "Content-Type": "text/plain"})  # fmt: skip
    return str(ok(r, 201)["id"])


async def lines(client: AsyncClient, w: dict[str, Any]) -> list[str]:
    ticket = ok(await client.get(w["url"], headers=w["admin"]))
    return [m["body"] for m in ticket["messages"] if m["kind"] == "system"]


async def test_a_file_is_handed_out_once_found_clean(
    app_parts: tuple[AsyncClient, Any], app_sessionmaker: Sm
) -> None:
    client = app_parts[0]
    w = await desk(app_parts, app_sessionmaker)
    f = await upload(client, w, "invoice.txt", b"total 499")
    r = await client.get(f"/api/v1/attachments/{f}", headers=w["admin"])
    assert r.status_code == 409 and "still being checked" in r.text

    result = await scan_due(app_sessionmaker, w["store"], FakeScanner())
    assert result.clean >= 1 and not result.waiting
    found = ok(await client.get(f"/api/v1/attachments/{f}", headers=w["admin"]))
    assert found["attachment"]["scan_status"] == "clean"
    assert (await client.get(found["url"])).content == b"total 499"


async def test_an_infected_file_is_removed_and_blocked(
    app_parts: tuple[AsyncClient, Any], app_sessionmaker: Sm
) -> None:
    client = app_parts[0]
    w = await desk(app_parts, app_sessionmaker)
    f = await upload(client, w, "eicar.com", EICAR)
    result = await scan_due(app_sessionmaker, w["store"], FakeScanner())
    assert result.infected == 1

    r = await client.get(f"/api/v1/attachments/{f}", headers=w["admin"])
    assert r.status_code == 409 and "virus scan" in r.text
    assert "File eicar.com was blocked: a virus was found (Eicar-Test-Signature)." in (
        await lines(client, w)
    )
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        row = (await s.execute(text("SELECT object_key, scan_result, scanned_at FROM hd_attachment "
                                    "WHERE id = :id"), {"id": f})).one()  # fmt: skip
    assert row.scan_result == "Eicar-Test-Signature" and row.scanned_at is not None
    with pytest.raises(ObjectMissing):
        await w["raw_store"].get(row.object_key)  # the bytes are gone
    r = await client.post(f"{w['url']}/messages", headers=w["admin"],
                          json={"kind": "note", "body": "see", "attachment_ids": [f]})  # fmt: skip
    assert r.status_code == 422


async def test_a_file_the_scanner_cannot_check_is_blocked(
    app_parts: tuple[AsyncClient, Any], app_sessionmaker: Sm
) -> None:
    client = app_parts[0]
    w = await desk(app_parts, app_sessionmaker)
    f = await upload(client, w, "huge.bin", b"HUGE")
    scanner = FakeScanner()
    scanner.cannot = b"HUGE"
    assert (await scan_due(app_sessionmaker, w["store"], scanner)).failed == 1
    found = ok(await client.get(f"{w['url']}/attachments", headers=w["admin"]))
    assert [a["scan_status"] for a in found["attachments"]] == ["scan_failed"]
    r = await client.get(f"/api/v1/attachments/{f}", headers=w["admin"])
    assert r.status_code == 409 and "could not be checked" in r.text
    assert any("huge.bin was blocked: it could not be checked" in b for b in await lines(client, w))


async def test_when_the_scanner_is_down_files_wait(
    app_parts: tuple[AsyncClient, Any], app_sessionmaker: Sm
) -> None:
    client = app_parts[0]
    w = await desk(app_parts, app_sessionmaker)
    f = await upload(client, w, "photo.txt", b"fine")
    scanner = FakeScanner()
    scanner.down = True
    result = await scan_due(app_sessionmaker, w["store"], scanner)
    assert result.waiting
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        status = await s.scalar(text("SELECT scan_status FROM hd_attachment WHERE id = :id"),
                                {"id": f})  # fmt: skip
    assert status == "not_scanned"
    scanner.down = False
    await scan_due(app_sessionmaker, w["store"], scanner)
    ok(await client.get(f"/api/v1/attachments/{f}", headers=w["admin"]))


async def test_an_email_waits_for_its_files_to_be_checked(
    app_parts: tuple[AsyncClient, Any], app_sessionmaker: Sm
) -> None:
    client = app_parts[0]
    w = await desk(app_parts, app_sessionmaker, channel="email")
    f = await upload(client, w, "label.txt", b"label")
    reply = {"kind": "reply", "body": "Here", "attachment_ids": [f]}
    ok(await client.post(f"{w['url']}/messages", headers=w["admin"], json=reply), 201)
    mailer = Catch(w["domain"])
    await send_due(app_sessionmaker, mailer, store=w["raw_store"], scanning=True)
    assert mailer.sent == []  # held back, not failed

    await scan_due(app_sessionmaker, w["store"], FakeScanner())
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(text("UPDATE hd_email_out SET next_attempt_at = now()"))
    await send_due(app_sessionmaker, mailer, store=w["raw_store"], scanning=True)
    [email] = mailer.sent
    assert [a.name for a in email.attachments] == ["label.txt"]
