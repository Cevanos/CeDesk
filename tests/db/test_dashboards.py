"""Dashboards (feature-gap plan 3.3) through the real API with RLS on: cards that are saved
or built-in reports, personal and shared, what a shared dashboard may hold, running a card
under the viewer's scope with the page's range and team, caps and cascades."""

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, ok
from tests.db.report_helpers import desk, sql, team_only_member, ticket

pytestmark = pytest.mark.db

D = "/api/v1/dashboards"
S = "/api/v1/reports/saved"


async def report(client: AsyncClient, w: dict, who: str, name: str, **extra: object) -> dict:
    body = {"name": name, "definition": {"metric": "created", "group_by": "team"}} | extra
    return ok(await client.post(S, headers=w[who], json=body), 201)  # type: ignore[no-any-return]


async def board(
    client: AsyncClient, w: dict, who: str, name: str, cards: list, **extra: object
) -> dict:
    body = {"name": name, "cards": cards} | extra
    return ok(await client.post(D, headers=w[who], json=body), 201)  # type: ignore[no-any-return]


async def test_a_dashboard_holds_saved_and_built_in_cards_in_order(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    mine = await report(client, w, "sam", "By team")
    made = await board(client, w, "sam", " Daily   board ",
                       [{"preset": "created_per_day"}, {"report_id": mine["id"]}])  # fmt: skip
    assert made["name"] == "Daily board" and made["shared"] is False and made["can_edit"] is True
    assert [(c["position"], c["label"]) for c in made["cards"]] == [
        (0, "Tickets created per day"), (1, "By team"),
    ]  # fmt: skip
    assert (
        made["cards"][0]["preset"] == "created_per_day"
        and made["cards"][1]["report_id"] == mine["id"]
    )
    assert [d["id"] for d in ok(await client.get(D, headers=w["sam"]))] == [made["id"]]
    assert ok(await client.get(D, headers=w["admin"])) == []
    assert (await client.get(f"{D}/{made['id']}", headers=w["admin"])).status_code == 404


async def test_a_card_runs_on_its_own_with_the_pages_range_and_team(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    for team in ("Billing", "Shipping", "Shipping"):
        await ticket(client, w, "x", team=team)
    made = await board(client, w, "sam", "Board", [{"preset": "created_by_team"}])
    card = f"{D}/{made['id']}/cards/{made['cards'][0]['id']}/run"
    out = ok(await client.post(card, headers=w["sam"]))
    assert {g["label"]: g["value"] for g in out["groups"]} == {"Billing": 1, "Shipping": 2}
    narrowed = ok(
        await client.post(card, headers=w["sam"], json={"team_id": w["teams"]["Shipping"]})
    )
    assert {g["label"]: g["value"] for g in narrowed["groups"]} == {"Shipping": 2}
    ranged = ok(await client.post(card, headers=w["sam"], json={"range": {"preset": "last_7d"}}))
    assert ranged["total"] == 3
    assert (
        await client.post(card, headers=w["sam"], json={"range": {"preset": "nope"}})
    ).status_code == 422
    csv = await client.post(card, headers=w["sam"], params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert (
        await client.post(f"{D}/{made['id']}/cards/{made['id']}/run", headers=w["sam"])
    ).status_code == 404


async def test_a_shared_dashboard_may_only_hold_built_in_and_shared_reports(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    personal = await report(client, w, "sam", "Mine")
    shared_report = await report(client, w, "sam", "Team", shared=True)
    refused = await client.post(
        D,
        headers=w["sam"],
        json={"name": "S", "shared": True, "cards": [{"report_id": personal["id"]}]},
    )
    assert refused.status_code == 422 and "shared reports" in refused.text
    cards = [{"report_id": shared_report["id"]}, {"preset": "csat_trend"}]
    made = await board(client, w, "sam", "S", cards, shared=True)
    assert made["shared"] is True
    # Adding a personal report to it later is refused too, and so is sharing one that holds one.
    add = await client.patch(
        f"{D}/{made['id']}", headers=w["sam"], json={"cards": [{"report_id": personal["id"]}]}
    )
    assert add.status_code == 422
    holds = await board(client, w, "sam", "P", [{"report_id": personal["id"]}])
    assert (
        await client.patch(f"{D}/{holds['id']}", headers=w["sam"], json={"shared": True})
    ).status_code == 422
    # Another member sees the shared one, runs its cards, and can't change it.
    seen = ok(await client.get(D, headers=w["admin"]))
    assert [(d["id"], d["can_edit"]) for d in seen] == [(made["id"], True)]
    assert ok(
        await client.post(
            f"{D}/{made['id']}/cards/{made['cards'][0]['id']}/run", headers=w["admin"]
        )
    )


async def test_cards_are_checked(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    other = await report(client, w, "admin", "Admin's own")  # personal: Sam can't use it
    for cards in (
        [{"preset": "nope"}],
        [{"report_id": other["id"]}],
        [{"report_id": "00000000-0000-4000-8000-000000000000"}],
        [{"report_id": "not-an-id"}],
        [{}],
        [{"preset": "csat_trend", "report_id": other["id"]}],
        [{"preset": "csat_trend", "extra": 1}],
        [{"preset": "csat_trend"}] * 21,
    ):
        r = await client.post(D, headers=w["sam"], json={"name": "x", "cards": cards})
        assert r.status_code == 422, cards
    assert (
        await client.post(D, headers=w["neha"], json={"name": "x", "cards": []})
    ).status_code == 403


async def test_changing_and_reordering_cards_renaming_and_deleting(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await board(
        client, w, "sam", "Board", [{"preset": "created_per_day"}, {"preset": "csat_trend"}]
    )
    path = f"{D}/{made['id']}"
    swapped = ok(
        await client.patch(
            path,
            headers=w["sam"],
            json={
                "cards": [{"preset": "csat_trend"}, {"preset": "created_per_day"}],
                "name": "Renamed",
            },
        )
    )
    assert swapped["name"] == "Renamed"
    assert [c["preset"] for c in swapped["cards"]] == ["csat_trend", "created_per_day"]
    emptied = ok(await client.patch(path, headers=w["sam"], json={"cards": []}))
    assert emptied["cards"] == []
    assert (await client.patch(path, headers=w["admin"], json={"name": "x"})).status_code == 404
    assert (await client.delete(path, headers=w["admin"])).status_code == 404
    assert (await client.delete(path, headers=w["sam"])).status_code == 204
    entries = ok(
        await client.get("/api/v1/audit", headers=w["admin"], params={"area": "dashboards"})
    )
    actions = sorted(e["action"] for e in entries["entries"])
    assert actions == ["changed", "changed", "created", "deleted"]


async def test_names_caps_and_the_cascade_when_a_saved_report_goes(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    saved = await report(client, w, "sam", "Goes away")
    made = await board(
        client, w, "sam", "Board", [{"report_id": saved["id"]}, {"preset": "csat_trend"}]
    )
    assert (
        await client.post(D, headers=w["sam"], json={"name": "BOARD", "cards": []})
    ).status_code == 409
    assert (await client.delete(f"{S}/{saved['id']}", headers=w["sam"])).status_code == 204
    left = ok(await client.get(f"{D}/{made['id']}", headers=w["sam"]))
    assert [c["preset"] for c in left["cards"]] == ["csat_trend"]  # the card went with the report

    await sql(app_sessionmaker, w["ws"],
              "INSERT INTO hd_dashboard (workspace_id, owner_id, name, shared) "
              "SELECT :ws, CAST(:o AS uuid), 'd' || g, false FROM generate_series(1, 19) g",
              ws=w["ws"], o=w["ids"]["sam"])  # fmt: skip
    assert (
        await client.post(D, headers=w["sam"], json={"name": "too many", "cards": []})
    ).status_code == 422


async def test_a_report_on_a_shared_or_anothers_dashboard_cannot_be_deleted(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Deleting a saved report takes its cards with it (the foreign key cascades), so it is
    refused while a card is on a shared dashboard or on another member's own dashboard;
    the owner's cards on their personal dashboards go with it, and that is audited."""
    w = await desk(client, app_sessionmaker)
    saved = await report(client, w, "sam", "Pipeline", shared=True)
    path = f"{S}/{saved['id']}"
    theirs = await board(client, w, "admin", "Admin's", [{"report_id": saved["id"]}])
    refused = await client.delete(path, headers=w["sam"])
    assert refused.status_code == 409 and "dashboard" in refused.json()["error"]["message"]
    ok(await client.patch(f"{D}/{theirs['id']}", headers=w["admin"], json={"cards": []}))
    team = await board(client, w, "sam", "Team board", [{"report_id": saved["id"]}], shared=True)
    assert (await client.delete(path, headers=w["sam"])).status_code == 409  # shared, own
    ok(await client.patch(f"{D}/{team['id']}", headers=w["sam"], json={"cards": []}))
    own = await board(
        client, w, "sam", "Mine", [{"report_id": saved["id"]}, {"preset": "csat_trend"}]
    )
    assert (await client.delete(path, headers=w["sam"])).status_code == 204
    left = ok(await client.get(f"{D}/{own['id']}", headers=w["sam"]))
    assert [c["preset"] for c in left["cards"]] == ["csat_trend"]
    ok(await client.get(f"{D}/{theirs['id']}", headers=w["admin"]))  # emptied, still there
    ok(await client.get(f"{D}/{team['id']}", headers=w["sam"]))
    entries = ok(
        await client.get("/api/v1/audit", headers=w["admin"], params={"area": "dashboards"})
    )["entries"]
    removed = [e for e in entries if e["target_id"] == own["id"] and e["action"] == "changed"]
    assert len(removed) == 1 and removed[0]["detail"] == {"report_id": saved["id"]}
    reports = ok(await client.get("/api/v1/audit", headers=w["admin"], params={"area": "reports"}))
    deleted = [e for e in reports["entries"] if e["action"] == "deleted"]
    assert deleted[0]["detail"]["dashboard_cards"] == 1


async def test_a_card_whose_report_was_unshared_keeps_no_name_or_details(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    theirs = await report(client, w, "sam", "Secret pipeline", shared=True)
    made = await board(client, w, "admin", "Mine", [{"report_id": theirs["id"]}])
    assert made["cards"][0]["label"] == "Secret pipeline"  # shared: visible
    ok(await client.patch(f"{S}/{theirs['id']}", headers=w["sam"], json={"shared": False}))
    for got in (
        ok(await client.get(f"{D}/{made['id']}", headers=w["admin"])),
        ok(await client.get(D, headers=w["admin"]))[0],
    ):
        assert [c["label"] for c in got["cards"]] == ["Report not available"]
        assert "Secret pipeline" not in str(got) and "definition" not in str(got)
    run = f"{D}/{made['id']}/cards/{made['cards'][0]['id']}/run"
    assert (await client.post(run, headers=w["admin"])).status_code == 404
    # Shared again, the name is back; and the owner always sees their own report's name.
    ok(await client.patch(f"{S}/{theirs['id']}", headers=w["sam"], json={"shared": True}))
    again = ok(await client.get(f"{D}/{made['id']}", headers=w["admin"]))
    assert again["cards"][0]["label"] == "Secret pipeline"
    own = await board(client, w, "sam", "Own", [{"report_id": theirs["id"]}])
    ok(await client.patch(f"{S}/{theirs['id']}", headers=w["sam"], json={"shared": False}))
    assert ok(await client.get(f"{D}/{own['id']}", headers=w["sam"]))["cards"][0]["label"] == (
        "Secret pipeline"
    )


async def test_a_team_only_viewer_runs_a_shared_card_in_their_team_and_isolation(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    a = await desk(client, app_sessionmaker)
    b = await desk(client, app_sessionmaker)
    for team in ("Billing", "Shipping"):
        await ticket(client, a, "x", team=team)
    made = await board(client, a, "sam", "Shared", [{"preset": "created_by_team"}], shared=True)
    ravi = await team_only_member(client, a, "Billing")
    card = f"{D}/{made['id']}/cards/{made['cards'][0]['id']}/run"
    assert {g["label"] for g in ok(await client.post(card, headers=ravi))["groups"]} == {"Billing"}
    assert ok(await client.get(D, headers=b["admin"])) == []
    assert (await client.post(card, headers=b["admin"])).status_code == 404
