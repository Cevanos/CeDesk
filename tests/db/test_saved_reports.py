"""Saved reports (feature-gap plan 3.2) through the real API with RLS on: personal and shared,
who may share, names, caps, a preset range only, running under the viewer's own scope,
audit, and the guards on unsharing."""

from uuid import UUID

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.report_helpers import desk, sql, team_only_member, ticket

pytestmark = pytest.mark.db

R = "/api/v1/reports/saved"
CREATED = {"metric": "created", "group_by": "team", "range": {"preset": "last_30d"}}


def post(client: AsyncClient, w: dict, who: str, name: str = "By team", **extra: object):
    return client.post(R, headers=w[who], json={"name": name, "definition": CREATED} | extra)


async def test_a_saved_report_is_personal_until_shared_and_runs_afresh(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = ok(await post(client, w, "sam", "  Created   by team "), 201)
    assert (made["name"], made["shared"], made["can_edit"], made["owner_name"]) == (
        "Created by team", False, True, "Sam",
    )  # fmt: skip
    assert made["definition"]["range"] == {"preset": "last_30d"}
    assert [r["id"] for r in ok(await client.get(R, headers=w["sam"]))] == [made["id"]]
    assert ok(await client.get(R, headers=w["admin"])) == []  # personal: not shared with anyone
    assert (await client.get(f"{R}/{made['id']}", headers=w["admin"])).status_code == 404

    await ticket(client, w, "x", team="Billing")
    out = ok(await client.post(f"{R}/{made['id']}/run", headers=w["sam"]))
    assert {g["label"]: g["value"] for g in out["groups"]} == {"Billing": 1}


async def test_only_roles_with_the_share_right_share_and_everyone_with_reports_sees_it(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    shared = ok(await post(client, w, "sam", "Team view", shared=True), 201)
    admin_view = ok(await client.get(R, headers=w["admin"]))
    assert [(r["id"], r["can_edit"]) for r in admin_view] == [(shared["id"], True)]
    assert (await post(client, w, "neha", "x")).status_code == 403  # no reports right at all
    assert (await client.get(R, headers=w["lisa"])).status_code == 403

    # A custom role with the reports right but not the share right: sees, runs, can't share.
    reader = {"name": "Reader", "scope": "all", "reports": True}
    role = ok(await client.post("/api/v1/roles", headers=w["admin"], json=reader), 201)
    member = f"/api/v1/members/{w['ids']['ravi']}"
    ok(
        await client.patch(
            member, headers=w["admin"], json={"role": "custom", "role_id": role["id"]}
        )
    )
    from tests.db.helpers import bearer, sign_in

    ravi = bearer(await sign_in(client, w["slug"], "ravi@example.com"))
    seen = ok(await client.get(R, headers=ravi))
    assert [(r["id"], r["can_edit"]) for r in seen] == [(shared["id"], False)]
    assert (await client.post(f"{R}/{shared['id']}/run", headers=ravi)).status_code == 200
    mine = await client.post(R, headers=ravi, json={"name": "Mine", "definition": CREATED})
    assert mine.status_code == 201
    refused = await client.post(
        R, headers=ravi, json={"name": "S", "definition": CREATED, "shared": True}
    )
    assert refused.status_code == 403
    assert (
        await client.patch(f"{R}/{shared['id']}", headers=ravi, json={"name": "x"})
    ).status_code == 403
    assert (await client.delete(f"{R}/{shared['id']}", headers=ravi)).status_code == 403


async def test_names_are_unique_per_owner_and_among_shared_ignoring_case(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ok(await post(client, w, "sam", "Weekly"), 201)
    assert (await post(client, w, "sam", "WEEKLY")).status_code == 409
    ok(await post(client, w, "admin", "Weekly"), 201)  # another owner may reuse a personal name
    ok(await post(client, w, "sam", "Team", shared=True), 201)
    assert (await post(client, w, "admin", "team", shared=True)).status_code == 409
    for bad in ("", "   ", "x" * 81):
        assert (await post(client, w, "sam", bad)).status_code == 422


async def test_a_definition_must_be_valid_and_use_a_preset_range(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    fixed = {
        "metric": "created",
        "range": {"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"},
    }
    for bad in (
        fixed,
        {"metric": "nope"},
        {"metric": "open_now", "group_by": "day"},
        {"metric": "created", "filters": {"team": "00000000-0000-4000-8000-000000000000"}},
        "not an object",
    ):
        r = await client.post(R, headers=w["sam"], json={"name": "x", "definition": bad})
        assert r.status_code == 422, bad
    assert (
        "preset"
        in (await client.post(R, headers=w["sam"], json={"name": "x", "definition": fixed})).text
    )


async def test_changing_renaming_and_sharing(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    made = ok(await post(client, w, "sam", "Mine"), 201)
    path = f"{R}/{made['id']}"
    renamed = ok(await client.patch(path, headers=w["sam"], json={"name": "Renamed"}))
    assert renamed["name"] == "Renamed" and renamed["definition"] == made["definition"]
    redefined = ok(
        await client.patch(
            path,
            headers=w["sam"],
            json={
                "definition": {
                    "metric": "resolved",
                    "group_by": "day",
                    "range": {"preset": "last_7d"},
                }
            },
        )
    )
    assert redefined["definition"]["metric"] == "resolved"
    assert (
        await client.patch(path, headers=w["sam"], json={"definition": {"metric": "x"}})
    ).status_code == 422
    assert (await client.patch(path, headers=w["sam"], json={})).status_code in (200, 422)
    assert (
        await client.patch(path, headers=w["admin"], json={"name": "Not yours"})
    ).status_code == 404
    shared = ok(await client.patch(path, headers=w["sam"], json={"shared": True}))
    assert shared["shared"] is True
    back = ok(await client.patch(path, headers=w["sam"], json={"shared": False}))
    assert back["shared"] is False


async def test_the_caps_on_personal_and_shared_reports(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    sam = w["ids"]["sam"]
    await sql(
        app_sessionmaker, w["ws"],
        "INSERT INTO hd_saved_report (workspace_id, owner_id, name, definition, shared) "
        "SELECT :ws, :owner, 'r' || g, CAST('{\"metric\": \"created\"}' AS jsonb), false "
        "FROM generate_series(1, 50) g",
        ws=w["ws"], owner=UUID(sam),
    )  # fmt: skip
    assert (await post(client, w, "sam", "one too many")).status_code == 422
    ok(await post(client, w, "admin", "fine"), 201)  # the cap is per member


async def test_a_viewer_runs_it_under_their_own_team_scope(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for team in ("Billing", "Shipping", "Shipping"):
        await ticket(client, w, "x", team=team)
    shared = ok(await post(client, w, "sam", "By team", shared=True), 201)
    ravi = await team_only_member(client, w, "Billing")
    out = ok(await client.post(f"{R}/{shared['id']}/run", headers=ravi))
    assert {g["label"]: g["value"] for g in out["groups"]} == {"Billing": 1}
    everyone = ok(await client.post(f"{R}/{shared['id']}/run", headers=w["sam"]))
    assert {g["label"]: g["value"] for g in everyone["groups"]} == {"Billing": 1, "Shipping": 2}


async def test_a_saved_report_runs_as_csv_and_a_broken_one_says_so(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = ok(await post(client, w, "sam"), 201)
    r = await client.post(f"{R}/{made['id']}/run", headers=w["sam"], params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    broken_json = 'UPDATE hd_saved_report SET definition = CAST(\'{"metric": "gone"}\' AS jsonb)'
    await sql(app_sessionmaker, w["ws"], broken_json)
    broken = await client.post(f"{R}/{made['id']}/run", headers=w["sam"])
    assert broken.status_code == 422 and "no longer valid" in broken.text


async def test_every_change_is_audited_without_the_numbers(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = ok(await post(client, w, "sam", "Audited"), 201)
    ok(await client.patch(f"{R}/{made['id']}", headers=w["sam"], json={"name": "Audited 2"}))
    assert (await client.delete(f"{R}/{made['id']}", headers=w["sam"])).status_code == 204
    assert (await client.delete(f"{R}/{made['id']}", headers=w["sam"])).status_code == 404
    entries = ok(await client.get("/api/v1/audit", headers=w["admin"], params={"area": "reports"}))
    assert sorted(e["action"] for e in entries["entries"]) == ["changed", "created", "deleted"]
    assert all(e["target_id"] == made["id"] for e in entries["entries"])


async def test_one_workspace_never_sees_anothers_reports(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    shared = ok(await post(client, a, "sam", "Shared", shared=True), 201)
    assert ok(await client.get(R, headers=b["admin"])) == []
    assert (await client.get(f"{R}/{shared['id']}", headers=b["admin"])).status_code == 404
    assert (await client.post(f"{R}/{shared['id']}/run", headers=b["admin"])).status_code == 404


async def test_a_report_on_a_shared_dashboard_or_in_anothers_schedule_cannot_be_unshared(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    report = ok(await post(client, w, "sam", "Team view", shared=True), 201)
    path = f"{R}/{report['id']}"
    board = ok(
        await client.post(
            "/api/v1/dashboards",
            headers=w["sam"],
            json={"name": "Board", "shared": True, "cards": [{"report_id": report["id"]}]},
        ),
        201,
    )
    refused = await client.patch(path, headers=w["sam"], json={"shared": False})
    assert refused.status_code == 409 and "shared dashboard" in refused.text
    assert (
        await client.delete(f"/api/v1/dashboards/{board['id']}", headers=w["sam"])
    ).status_code == 204

    # Admin schedules Sam's shared report: Sam can't pull it out from under that schedule.
    ok(
        await client.post(
            "/api/v1/report-schedules",
            headers=w["admin"],
            json={
                "name": "Weekly",
                "report_id": report["id"],
                "cadence": "weekly",
                "weekday": 1,
                "hour": 9,
                "recipient_ids": [w["ids"]["sam"]],
            },
        ),
        201,
    )
    refused = await client.patch(path, headers=w["sam"], json={"shared": False})
    assert refused.status_code == 409 and "scheduled report" in refused.text
