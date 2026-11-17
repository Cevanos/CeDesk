"""New credentials for a connection whose key the source refused, then resume
(`POST /imports/connections/{id}/credentials`). Real PostgreSQL with RLS on; a fake source
that reads the key it was built with, as the real connectors do.

- the run fails on a 401 (the key was revoked); the Admin gives a new key for the same
  account and resumes; the run finishes from its saved page, nothing twice;
- a key for another account is refused (a run never mixes two accounts), as is one the
  source refuses, one for a closed connection, and any caller but an Admin;
- the credentials are never echoed: not in the response, the history or the audit log.
"""

import json
from collections.abc import Mapping
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain.imports import AccountInfo, Page, RecordFailures, SourceError
from cedesk.helpdesk.service.imports import framework
from tests.db.helpers import Sm, ok
from tests.db.test_import_failures import Flaky, im, pass_, run_row
from tests.db.test_import_resume import ONE_TRY, resume
from tests.db.test_imports import SETTINGS, I, desk, end_runs, sql, start

pytestmark = pytest.mark.db

__all__ = ["im"]  # the fixture, shared


class Keyed(Flaky):
    """Reads with the key it was built with: `revoked` keys are refused (401) from page 5
    on; `accounts` says which account a key reaches."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.key = ""
        self.revoked: set[str] = set()
        self.accounts: dict[str, str] = {}

    async def check(self) -> AccountInfo:
        if self.key in self.revoked:
            raise SourceError("Zendesk refused these credentials.", permanent=True)
        return AccountInfo(self.accounts.get(self.key, "acme.zendesk.com"))

    async def page(self, kind: str, cursor: Mapping[str, Any] | None) -> Page[Any]:
        if self.key in self.revoked and cursor and int(cursor["offset"]) >= 5:
            raise SourceError("Zendesk refused these credentials.", permanent=True)
        return await super().page(kind, cursor)


def keyed_importers(im: AsyncClient, src: Keyed) -> framework.Importers:
    app = im.desk_app  # type: ignore[attr-defined]

    async def build(name: str, settings: Mapping[str, Any], secret: str | None) -> Keyed:
        src.failures = RecordFailures()
        src.key = json.loads(secret or "{}").get("token", "")
        return src

    found = framework.make_importers(
        SETTINGS.secret_key.get_secret_value(), app.state.store, build=build, retry=ONE_TRY
    )
    app.state.importers = found
    return found


async def revoked_run(im: AsyncClient, sm: Sm) -> tuple[Keyed, Any, dict[str, Any], Any, str]:
    src = Keyed(tickets=12)
    importers = keyed_importers(im, src)
    w = await desk(im, sm)
    conn = ok(await im.post(f"{I}/connections", headers=w["admin"], json={
        "source": "zendesk", "settings": {"subdomain": "acme"},
        "credentials": {"token": "old-key-1111"}}), 201)  # fmt: skip
    run_id = await start(im, w, conn, kinds=["tickets"])
    src.revoked = {"old-key-1111"}  # revoked in Zendesk after page 0 was read
    await pass_(sm, importers, w["ws"])
    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "failed" and row["cursor"]["page"] == {"offset": 5}
    return src, importers, w, conn, run_id


def new_key(im: AsyncClient, w: dict[str, Any], conn: dict[str, Any], key: str) -> Any:
    return im.post(
        f"{I}/connections/{conn['id']}/credentials", headers=w["admin"],
        json={"credentials": {"token": key}},
    )  # fmt: skip


async def test_new_credentials_then_resume_finishes_the_import(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    _, importers, w, conn, run_id = await revoked_run(im, sm)

    out = ok(await new_key(im, w, conn, "new-key-2222"))
    assert out["credentials_hint"] == "2222" and out["state"] == "connected"
    assert "new-key" not in json.dumps(out)
    ok(await resume(im, w, run_id), 202)
    await pass_(sm, importers, w["ws"])

    row = await run_row(sm, w["ws"], run_id)
    assert row["state"] == "done" and row["counts"]["tickets"]["created"] == 12
    ids = [r["source_id"] for r in await sql(sm, w["ws"], "SELECT source_id FROM hd_ticket")]
    assert len(ids) == len(set(ids)) == 12
    [event] = await sql(sm, w["ws"], "SELECT detail FROM hd_import_event "
                        "WHERE kind = 'credentials_updated'")  # fmt: skip
    audit = await sql(
        sm, w["ws"], "SELECT detail FROM hd_audit WHERE action = 'credentials_updated'"
    )
    assert len(audit) == 1
    for said in (event["detail"], audit[0]["detail"]):
        assert said == {"account": "acme.zendesk.com"} and "key" not in json.dumps(said)
    await end_runs(sm, w["ws"])


async def test_credentials_for_another_account_are_refused_and_change_nothing(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    src, _, w, conn, _ = await revoked_run(im, sm)
    src.accounts = {"other-3333": "globex.zendesk.com"}
    [before] = await sql(sm, w["ws"], "SELECT credentials_sealed FROM hd_import_connection")

    r = await new_key(im, w, conn, "other-3333")
    assert r.status_code == 409 and "another account" in r.json()["error"]["message"]
    r = await new_key(im, w, conn, "old-key-1111")  # still revoked: the source refuses it
    assert r.status_code == 422
    assert (await im.post(f"{I}/connections/{conn['id']}/credentials", headers=w["asha"],
                          json={"credentials": {"token": "x"}})).status_code == 403  # fmt: skip

    [after] = await sql(sm, w["ws"], "SELECT credentials_sealed FROM hd_import_connection")
    assert after == before
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_import_event "
                     "WHERE kind = 'credentials_updated'") == []  # fmt: skip
    await end_runs(sm, w["ws"])


async def test_a_closed_connection_cant_take_new_credentials(
    im: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    _, _, w, conn, _ = await revoked_run(im, sm)
    closed = await im.post(f"{I}/connections/{conn['id']}/close", headers=w["admin"])
    assert closed.status_code == 204
    r = await new_key(im, w, conn, "new-key-2222")
    assert r.status_code == 409 and "closed" in r.json()["error"]["message"]
    await end_runs(sm, w["ws"])
