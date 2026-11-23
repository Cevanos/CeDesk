"""Contact CSV import and export (feature-gap plan 2.5) with RLS on: a dry run with
per-row errors writes nothing; the run creates and updates by email (also merged-in
addresses) as `on_existing` says; erased customers are never touched; the export is
Admin-only, safe for spreadsheets, leaves out erased customers, and both are audited
without addresses; another workspace's contacts never appear."""

import csv
import io
import json
from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import as_, audit, desk, sql
from tests.db.test_contacts import C, customer, field

pytestmark = pytest.mark.db

FILE = (
    "E-mail,Full name,Phone,Company,VIP,Plan\n"
    "new@shop.test,New Person,111,Shop,yes,pro\n"
    "ANA@shop.test,Ana Renamed,222,,no,free\n"
    "not-an-email,Bad,,,,\n"
    "new@shop.test,Twice,,,,\n"
    "late@shop.test,Late,,,maybe,\n"
    "plan@shop.test,Plan,,,,gold\n"
    ",,,,,\n"
)


async def upload(client: AsyncClient, h: dict[str, str], data: str | bytes, **params: Any) -> Any:
    if "mapping" in params:
        params["mapping"] = json.dumps(params["mapping"])
    content = data.encode() if isinstance(data, str) else data
    return await client.post(
        f"{C}/import", headers={**h, "Content-Type": "text/csv"}, params=params, content=content
    )


async def test_a_dry_run_then_the_import(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await field(client, w["h"], "plan", "select", options=["free", "pro"]), 201)
    ana, _ = await customer(client, w, "Ana", "ana@shop.test")
    # Without a mapping, the columns are matched by name; nothing is written.
    dry = ok(await upload(client, w["h"], FILE))
    assert dry["dry_run"] and dry["mapping"] == {
        "email": "E-mail",
        "name": "Full name",
        "phone": "Phone",
        "organization": "Company",
        "vip": "VIP",
        "attributes": {"plan": "Plan"},
    }
    assert (dry["rows"], dry["created"], dry["updated"], dry["unchanged"]) == (6, 1, 1, 0)
    assert [(e["line"], e["reason"]) for e in dry["errors"]] == [
        (4, "No valid email address."),
        (5, "The same email as line 2."),
        (6, "VIP is true or false (yes/no, 1/0)."),
        (7, "Plan must be one of: free, pro."),
    ]
    assert dry["error_count"] == 4
    assert [c["name"] for c in ok(await client.get(C, headers=w["h"]))["contacts"]] == ["Ana"]
    # The run: Ana keeps her name (fill_blanks) and gains a phone and plan.
    done = ok(await upload(client, w["h"], FILE, dry_run="false"))
    assert (done["created"], done["updated"], done["error_count"]) == (1, 1, 4)
    found = {c["email"]: c for c in ok(await client.get(C, headers=w["h"]))["contacts"]}
    assert (found["new@shop.test"]["name"], found["new@shop.test"]["vip"]) == ("New Person", True)
    assert found["new@shop.test"]["attributes"] == {"plan": "pro"}
    a = found["ana@shop.test"]
    assert (a["id"], a["name"], a["phone"], a["vip"], a["attributes"]) == (
        ana,
        "Ana",
        "222",
        False,
        {"plan": "free"},
    )
    # Again: nothing new; overwrite takes the file's values; skip leaves them.
    again = ok(await upload(client, w["h"], FILE, dry_run="false"))
    assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 2)
    over = ok(await upload(client, w["h"], FILE, dry_run="false", on_existing="overwrite"))
    assert over["updated"] == 1
    assert ok(await client.get(f"{C}/{ana}", headers=w["h"]))["name"] == "Ana Renamed"
    skip = ok(
        await upload(
            client, w["h"], FILE.replace("Ana Renamed", "X"), dry_run="false", on_existing="skip"
        )
    )
    assert skip["unchanged"] == 2
    entries = [e for e in await audit(client, w, "contacts") if e["action"] == "imported"]
    assert len(entries) == 4 and entries[0]["detail"]["created"] == 0
    assert "shop.test" not in json.dumps(entries)


async def test_mapping_limits_and_who_may_import(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    data = "Address,Who\na@x.test,A\n"
    assert (await upload(client, w["h"], data)).status_code == 422  # no email column
    got = ok(await upload(client, w["h"], data, mapping={"email": "Address", "name": "Who"}))
    assert got["created"] == 1
    for mapping in (
        {"email": "Nope"},
        {"name": "Who"},
        {"email": "Address", "colour": "Who"},
        {"email": "Address", "attributes": {"plan": "Who"}},
    ):
        assert (await upload(client, w["h"], data, mapping=mapping)).status_code == 422, mapping
    r = await upload(client, w["h"], data, mapping={"email": "Address"}, on_existing="replace")
    assert r.status_code == 422
    assert (await upload(client, w["h"], b"Email\n\xff\xfe@x\n")).status_code == 422
    big = "Email\n" + "".join(f"u{i}@x.test\n" for i in range(5001))
    assert (await upload(client, w["h"], big)).status_code == 422
    huge = "Email\n" + "a" * (2 * 1024 * 1024)
    assert (await upload(client, w["h"], huge)).status_code == 413
    for who in ("Sam", "Neha", "Meena"):
        r = await upload(client, await as_(client, w, who), data)
        assert r.status_code == 403, who
    # An erased customer's address is never changed by an import.
    gone, _ = await customer(client, w, "Gone", "gone@x.test")
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c", c=gone
    )
    r = ok(await upload(client, w["h"], "Email,Name\ngone@x.test,Back\n", dry_run="false"))
    assert (r["created"], r["error_count"]) == (0, 1)
    assert r["errors"][0]["reason"] == "This customer was erased."


def rows(body: bytes) -> list[dict[str, str]]:
    text = body.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


async def test_the_export(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    them = await desk(client, app_sessionmaker)
    ok(await field(client, w["h"], "plan", "text"), 201)
    ana, _ = await customer(client, w, "=HYPERLINK(1)", "ana@shop.test")
    ok(
        await client.put(
            f"{C}/{ana}/attributes", headers=w["h"], json={"attributes": {"plan": "+pro"}}
        )
    )
    ok(await client.patch(f"{C}/{ana}/profile", headers=w["h"], json={"vip": True}))
    bo, _ = await customer(client, w, "Bo", "bo@shop.test")
    gone, _ = await customer(client, w, "Gone", "gone@shop.test")
    await sql(
        app_sessionmaker, w["ws"], "UPDATE hd_contact SET erased_at = now() WHERE id = :c", c=gone
    )
    await customer(client, them, "Theirs", "theirs@else.test")
    r = await client.get(f"{C}/export", headers=w["h"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    got = rows(r.content)
    assert sorted(x["email"] for x in got) == ["ana@shop.test", "bo@shop.test"]  # not "gone"
    a = next(x for x in got if x["id"] == ana)
    assert (a["name"], a["vip"], a["attr.plan"], a["tickets"]) == (
        "'=HYPERLINK(1)",
        "true",
        "'+pro",
        "1",
    )
    # The list's filters apply.
    only = rows((await client.get(f"{C}/export", headers=w["h"], params={"vip": True})).content)
    assert [x["id"] for x in only] == [ana]
    assert bo not in [x["id"] for x in only]
    # Admins only; audited with the count, without the search words.
    for who in ("Sam", "Neha", "Meena"):
        r = await client.get(f"{C}/export", headers=await as_(client, w, who))
        assert r.status_code == 403, who
    assert (
        await client.get(f"{C}/export", headers=w["h"], params={"q": "ana@shop"})
    ).status_code == 200
    entries = [e for e in await audit(client, w, "contacts") if e["action"] == "exported"]
    assert [e["detail"]["rows"] for e in entries] == [1, 1, 2]
    assert entries[0]["detail"]["filters"] == {"search": True}
    assert "ana@" not in json.dumps(entries)
    # The other workspace's export holds only its own.
    theirs = rows((await client.get(f"{C}/export", headers=them["h"])).content)
    assert [x["email"] for x in theirs] == ["theirs@else.test"]
