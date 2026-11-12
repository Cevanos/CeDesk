"""Ticket attachments through the real API, with RLS on and files on a temporary disk."""

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.main import create_app
from cedesk.settings import Settings
from cedesk.storage import links
from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

MAX = 1024  # bytes, so the size limit is cheap to test


@pytest.fixture
def files(tmp_path: Path) -> Path:
    return tmp_path / "files"


def stored(root: Path) -> list[str]:
    """Every file under the storage folder, as a key."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
async def client(app_sessionmaker: Sm, files: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(storage_local_path=str(files), attachment_max_bytes=MAX))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def setup(c: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    admin = bearer(await sign_in(c, slug, "admin@example.com"))
    await add(c, admin["Authorization"].split()[1], "lee@example.com", "light_agent", "Lee")
    ticket = ok(
        await c.post(
            "/api/v1/tickets",
            headers=admin,
            json={"subject": "Broken parcel", "contact": {"email": "ana@shop.test", "name": "Ana"}},
        ),
        201,
    )
    me = ok(await c.get("/api/v1/me", headers=admin))
    return {
        "admin": admin,
        "admin_id": me["member"]["id"],
        "workspace_id": me["workspace"]["id"],
        "light": bearer(await sign_in(c, slug, "lee@example.com")),
        "ticket": ticket["id"],
        "url": f"/api/v1/tickets/{ticket['id']}/attachments",
    }


async def upload(
    c: AsyncClient,
    w: dict[str, Any],
    data: bytes = b"%PDF-1.7 hello",
    *,
    name: str = "Report Q3.pdf",
    mime: str = "application/pdf",
    who: str = "admin",
) -> Any:
    return await c.post(
        w["url"], params={"name": name}, content=data, headers={**w[who], "Content-Type": mime}
    )


async def test_upload_list_and_download_through_a_signed_link(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w, name="../../etc/Report Q3.pdf"), 201)
    assert file["name"] == "Report Q3.pdf"
    assert (file["size"], file["mime"], file["scan_status"]) == (
        14,
        "application/pdf",
        "not_scanned",
    )
    assert (file["ticket_id"], file["uploaded_by"], file["message_id"]) == (
        w["ticket"],
        w["admin_id"],
        None,
    )

    listed = ok(await client.get(w["url"], headers=w["admin"]))
    assert [a["id"] for a in listed["attachments"]] == [file["id"]]
    assert "object_key" not in listed["attachments"][0]

    link = ok(await client.get(f"/api/v1/attachments/{file['id']}", headers=w["admin"]))
    assert link["url"].startswith("http://t/api/v1/files/")
    assert link["attachment"]["id"] == file["id"]

    # The link works without signing in, and always downloads rather than opens.
    got = await client.get(link["url"])
    assert got.status_code == 200
    assert got.content == b"%PDF-1.7 hello"
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith('attachment; filename="Report Q3.pdf"')
    assert got.headers["x-content-type-options"] == "nosniff"

    # Stored under ids only: the name never becomes a path.
    assert stored(files) == [f"{w['workspace_id']}/{w['ticket']}/{file['id']}"]


async def test_a_changed_or_expired_link_is_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w), 201)
    url = ok(await client.get(f"/api/v1/attachments/{file['id']}", headers=w["admin"]))["url"]

    tampered = await client.get(url[:-3] + "AAA")
    assert tampered.status_code == 404
    assert tampered.json()["error"]["code"] == "NotFound"

    key = f"{w['workspace_id']}/{w['ticket']}/{file['id']}"
    expired = links.sign(
        links.FileLink(key, "x.pdf", "application/pdf", int(time.time()) - 1),
        links.link_key(Settings().secret_key.get_secret_value()),
    )
    gone = await client.get(f"/api/v1/files/{expired}")
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "Gone"


async def test_too_large_empty_and_form_uploads_are_refused(
    client: AsyncClient, app_sessionmaker: Sm, files: Path
) -> None:
    w = await setup(client, app_sessionmaker)
    big = await upload(client, w, b"x" * (MAX + 1))
    assert big.status_code == 413
    assert big.json()["error"] == {
        "code": "TooLarge",
        "message": "A file can be at most 1024 bytes.",
    }
    assert ok(await upload(client, w, b"x" * MAX), 201)["size"] == MAX

    empty = await upload(client, w, b"")
    assert empty.status_code == 422
    assert empty.json()["error"]["message"] == "The file is empty."

    form = await client.post(
        w["url"], params={"name": "a.txt"}, files={"file": ("a.txt", b"hi")}, headers=w["admin"]
    )
    assert form.status_code == 422
    assert "not a form" in form.json()["error"]["message"]

    no_name = await client.post(w["url"], content=b"hi", headers=w["admin"])
    assert no_name.status_code == 422
    assert len(stored(files)) == 1  # only the accepted one


async def test_an_odd_type_is_stored_as_plain_bytes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w, b"<html>", name="page.html", mime="not a type"), 201)
    assert file["mime"] == "application/octet-stream"


async def test_a_light_agent_can_attach_to_notes_and_read(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w, who="light"), 201)
    ok(await client.get(f"/api/v1/attachments/{file['id']}", headers=w["light"]))


async def test_another_workspace_sees_nothing(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w), 201)
    other = await setup(client, app_sessionmaker)

    for response in (
        await client.get(f"/api/v1/attachments/{file['id']}", headers=other["admin"]),
        await client.get(w["url"], headers=other["admin"]),
        await client.post(
            w["url"],
            params={"name": "x.txt"},
            content=b"x",
            headers={**other["admin"], "Content-Type": "text/plain"},
        ),
    ):
        assert response.status_code == 404, response.text


async def test_signed_out_and_unknown_are_refused(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    assert (await client.post(w["url"], params={"name": "a"}, content=b"x")).status_code == 401
    assert (await client.get(w["url"])).status_code == 401
    unknown = "/api/v1/tickets/00000000-0000-0000-0000-000000000000/attachments"
    assert (await client.get(unknown, headers=w["admin"])).status_code == 404
    missing = "/api/v1/attachments/00000000-0000-0000-0000-000000000000"
    assert (await client.get(missing, headers=w["admin"])).status_code == 404


async def test_an_infected_file_is_never_handed_out(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w), 201)
    async with workspace_session(app_sessionmaker, UUID(w["workspace_id"])) as s:
        await s.execute(
            text("UPDATE hd_attachment SET scan_status = 'infected' WHERE id = :id"),
            {"id": file["id"]},
        )
    refused = await client.get(f"/api/v1/attachments/{file['id']}", headers=w["admin"])
    assert refused.status_code == 409
    assert "virus scan" in refused.json()["error"]["message"]


async def test_files_come_a_page_at_a_time(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await setup(client, app_sessionmaker)
    ids = [ok(await upload(client, w, name=f"{n}.txt"), 201)["id"] for n in range(3)]
    first = ok(await client.get(w["url"], params={"limit": 2}, headers=w["admin"]))
    assert [a["id"] for a in first["attachments"]] == ids[:0:-1]
    second = ok(
        await client.get(
            w["url"], params={"limit": 2, "cursor": first["next_cursor"]}, headers=w["admin"]
        )
    )
    assert [a["id"] for a in second["attachments"]] == ids[:1]
    assert second["next_cursor"] is None
    bad = await client.get(w["url"], params={"cursor": "nonsense"}, headers=w["admin"])
    assert bad.status_code == 422


async def test_files_can_require_sign_in(client: AsyncClient, app_sessionmaker: Sm) -> None:
    """S5: with the setting on, the link is an API path that needs the Bearer token."""
    w = await setup(client, app_sessionmaker)
    file = ok(await upload(client, w, b"%PDF-1.7 secret"), 201)
    link = ok(await client.get(f"/api/v1/attachments/{file['id']}", headers=w["admin"]))
    assert (link["needs_sign_in"], "/files/" in link["url"]) == (False, True)

    ok(
        await client.put(
            "/api/v1/security-settings", headers=w["admin"], json={"attachments_need_sign_in": True}
        )
    )
    link = ok(await client.get(f"/api/v1/attachments/{file['id']}", headers=w["admin"]))
    assert link["needs_sign_in"] is True
    assert link["url"] == f"http://t/api/v1/attachments/{file['id']}/content"
    assert (await client.get(link["url"])).status_code == 401
    got = await client.get(link["url"], headers=w["light"])
    assert (got.status_code, got.content) == (200, b"%PDF-1.7 secret")
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "Report Q3.pdf" in got.headers["content-disposition"]
