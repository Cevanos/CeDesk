"""Custom roles (md A3-A5) through the real API with RLS on: Admins build them, members
get them, the rights and the ticket scope are enforced, and the ticket says why it is
read-only."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.db.helpers import Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"
R = "/api/v1/roles"


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    """Admin, Neha (agent, Billing) and Ravi (to get custom roles)."""
    slug = await new_workspace(sm, seed=True)
    token = await sign_in(client, slug, "admin@example.com")
    h = bearer(token)
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    ravi = await add(client, token, "ravi@example.com", "agent", "Ravi")
    teams = {t["name"]: t["id"] for t in ok(await client.get("/api/v1/teams", headers=h))}
    ok(
        await client.patch(
            f"/api/v1/teams/{teams['Billing']}", headers=h, json={"member_ids": [neha, ravi]}
        )
    )
    return {"slug": slug, "h": h, "neha": neha, "ravi": ravi, "teams": teams}


async def role(client: AsyncClient, w: dict[str, Any], **body: Any) -> Any:
    return ok(await client.post(R, headers=w["h"], json={"name": "Billing desk"} | body), 201)


async def give(client: AsyncClient, w: dict[str, Any], role_id: str) -> dict[str, str]:
    """Gives Ravi the role and signs him in."""
    ok(
        await client.patch(
            f"/api/v1/members/{w['ravi']}",
            headers=w["h"],
            json={"role": "custom", "role_id": role_id},
        )
    )
    return bearer(await sign_in(client, w["slug"], "ravi@example.com"))


async def ticket(client: AsyncClient, w: dict[str, Any], subject: str, team: str) -> Any:
    body = {
        "subject": subject,
        "contact": {"email": "priya@example.com", "name": "Priya"},
        "team_id": w["teams"][team],
    }
    return ok(await client.post(T, headers=w["h"], json=body), 201)


async def test_admins_build_roles_and_give_them(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    made = await role(
        client, w, reply=True, approve=True, scope="team_edit", team_id=w["teams"]["Billing"]
    )
    assert (made["scope"], made["team_name"], made["member_count"]) == ("team_edit", "Billing", 0)
    ravi = await give(client, w, made["id"])

    me = ok(await client.get("/api/v1/me", headers=ravi))
    assert (me["member"]["role"], me["member"]["role_name"]) == ("custom", "Billing desk")
    access = me["access"]
    assert (access["role_name"], access["scope"], access["team_name"]) == (
        "Billing desk",
        "team_edit",
        "Billing",
    )
    assert "approve" in access["actions"]
    assert "manage_access" not in access["actions"]
    listed = ok(await client.get(R, headers=w["h"]))
    assert [(r["name"], r["member_count"]) for r in listed] == [("Billing desk", 1)]

    # Only Admins manage roles.
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (await client.get(R, headers=neha)).status_code == 403
    assert (await client.post(R, headers=neha, json={"name": "x"})).status_code == 403


async def test_role_rules(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    billing = w["teams"]["Billing"]
    bad = [
        {"name": "x", "scope": "team_only"},  # no team
        {"name": "x", "scope": "all", "team_id": billing},  # a team with scope all
        {"name": "x", "scope": "team_edit", "team_id": w["ravi"]},  # not a team
    ]
    for body in bad:
        assert (await client.post(R, headers=w["h"], json=body)).status_code == 422
    made = await role(client, w, scope="team_only", team_id=billing)
    assert (await client.post(R, headers=w["h"], json={"name": "billing DESK"})).status_code == 409

    # role custom needs a role_id; a built-in role takes none.
    path = f"/api/v1/members/{w['ravi']}"
    assert (await client.patch(path, headers=w["h"], json={"role": "custom"})).status_code == 422
    assert (
        await client.patch(path, headers=w["h"], json={"role": "agent", "role_id": made["id"]})
    ).status_code == 422
    await give(client, w, made["id"])
    # In use: neither the role nor its team can be deleted.
    r = await client.delete(f"{R}/{made['id']}", headers=w["h"])
    assert (r.status_code, "1 member has this role" in r.json()["error"]["message"]) == (409, True)
    assert (await client.delete(f"/api/v1/teams/{billing}", headers=w["h"])).status_code == 409
    # Back to a built-in role: role_id is cleared, and the role can go.
    back = ok(await client.patch(path, headers=w["h"], json={"role": "agent"}))
    assert (back["role"], back["role_id"]) == ("agent", None)
    assert (await client.delete(f"{R}/{made['id']}", headers=w["h"])).status_code == 204


async def test_settings_right_never_reaches_members_or_roles(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ravi = await give(client, w, (await role(client, w, settings=True))["id"])
    # Settings work...
    ok(await client.post("/api/v1/teams", headers=ravi, json={"name": "VIP"}), 201)
    # ...but not members, roles or making anyone an Admin.
    assert (await client.post(R, headers=ravi, json={"name": "Boss"})).status_code == 403
    r = await client.patch(f"/api/v1/members/{w['neha']}", headers=ravi, json={"role": "admin"})
    assert r.status_code == 403
    r = await client.post(
        "/api/v1/members",
        headers=ravi,
        json={"email": "x@example.com", "name": "X", "role": "admin", "password": "long enough pw"},
    )
    assert r.status_code == 403


async def test_team_only_hides_other_teams_tickets(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ravi = await give(
        client,
        w,
        (
            await role(
                client,
                w,
                reply=True,
                reports=True,
                scope="team_only",
                team_id=w["teams"]["Billing"],
            )
        )["id"],
    )
    mine = await ticket(client, w, "Refund", "Billing")
    other = await ticket(client, w, "Parcel lost", "Shipping")

    page = ok(await client.get(T, headers=ravi))
    assert [t["id"] for t in page["tickets"]] == [mine["id"]]
    assert sum(page["counts"].values()) == 1
    assert ok(await client.get(T, headers=ravi, params={"q": "Parcel"}))["tickets"] == []
    for path in ("", "/messages", "/related", "/tasks", "/attachments"):
        r = await client.get(f"{T}/{other['id']}{path}", headers=ravi)
        assert r.status_code == 404, path
    assert (
        await client.post(
            f"{T}/{other['id']}/messages", headers=ravi, json={"kind": "note", "body": "hi"}
        )
    ).status_code == 404
    ok(await client.get(f"{T}/{mine['id']}", headers=ravi))
    # The customer's page lists only what the role sees.
    contact = mine["contact"]["id"]
    shown = ok(await client.get(f"/api/v1/contacts/{contact}/tickets", headers=ravi))
    assert [t["id"] for t in shown["tickets"]] == [mine["id"]]
    # Reports are about its own team.
    report = await client.get(
        "/api/v1/reports/team", headers=ravi, params={"team_id": w["teams"]["Shipping"]}
    )
    assert report.status_code == 404
    own_report = ok(await client.get("/api/v1/reports/team", headers=ravi))
    assert own_report["summary"]["created"] == 1  # the Billing ticket, not Shipping's


async def test_team_edit_views_other_tickets_read_only(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ravi = await give(
        client,
        w,
        (await role(client, w, reply=True, scope="team_edit", team_id=w["teams"]["Billing"]))["id"],
    )
    mine = await ticket(client, w, "Refund", "Billing")
    other = await ticket(client, w, "Parcel lost", "Shipping")

    seen = ok(await client.get(f"{T}/{other['id']}", headers=ravi))
    assert seen["can_edit"] is False
    assert seen["view_only_reason"] == "Your role Billing desk can edit Billing tickets only."
    assert len(ok(await client.get(T, headers=ravi))["tickets"]) == 2
    # Notes are fine; changes, replies and tools are not.
    ok(
        await client.post(
            f"{T}/{other['id']}/messages", headers=ravi, json={"kind": "note", "body": "FYI"}
        ),
        201,
    )
    for method, path, body in [
        ("PATCH", "", {"priority": "low"}),
        ("POST", "/resolve", None),
        ("POST", "/messages", {"kind": "reply", "body": "Hello"}),
        ("POST", "/tasks", {"text": "Call"}),
        ("POST", "/time", {"minutes": 5}),
    ]:
        r = await client.request(method, f"{T}/{other['id']}{path}", headers=ravi, json=body)
        assert r.status_code == 403, path
        assert r.json()["error"]["message"] == seen["view_only_reason"]

    own = ok(await client.get(f"{T}/{mine['id']}", headers=ravi))
    assert (own["can_edit"], own["view_only_reason"]) == (True, None)
    ok(await client.patch(f"{T}/{mine['id']}", headers=ravi, json={"priority": "low"}))
    assert ok(await client.get(f"{T}/{mine['id']}", headers=w["h"]))["can_edit"] is True


async def test_a_role_without_reply_takes_no_tickets_and_changes_apply_at_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    made = await role(client, w, reply=False)
    ravi = await give(client, w, made["id"])
    t = await ticket(client, w, "Refund", "Billing")
    assert t["assignee_id"] == w["neha"]  # routing skips Ravi
    r = await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"assignee_id": w["ravi"]})
    assert r.status_code == 422
    assert r.json()["error"]["message"] == (
        f"Ravi can't be assigned tickets: the {made['name']} role doesn't reply to customers."
    )
    assert (
        await client.post(
            f"{T}/{t['id']}/messages", headers=ravi, json={"kind": "reply", "body": "Hi"}
        )
    ).status_code == 403
    assert ok(await client.get(f"{T}/{t['id']}", headers=ravi))["view_only_reason"] is None
    agents = ok(await client.get("/api/v1/agents", headers=w["h"]))
    listed = next(a for a in agents if a["id"] == w["ravi"])
    assert (listed["status"], listed["assignable"]) == (None, False)  # not in a picker

    # The Admin ticks reply: Ravi can reply on his next request, no new sign-in.
    body = {
        k: made[k]
        for k in ("name", "assign_any", "reports", "settings", "approve", "scope", "team_id")
    } | {"reply": True}
    ok(await client.put(f"{R}/{made['id']}", headers=w["h"], json=body))
    ok(
        await client.post(
            f"{T}/{t['id']}/messages", headers=ravi, json={"kind": "reply", "body": "Hi"}
        ),
        201,
    )


async def test_a_custom_role_with_approve_can_be_asked(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    ravi = await give(client, w, (await role(client, w, approve=True))["id"])
    t = await ticket(client, w, "Refund", "Billing")
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    asked = ok(
        await client.post(
            f"{T}/{t['id']}/approvals",
            headers=neha,
            json={"approver_id": w["ravi"], "what": "₹900"},
        ),
        201,
    )
    done = ok(
        await client.post(
            f"/api/v1/approvals/{asked['id']}/decide", headers=ravi, json={"decision": "approve"}
        )
    )
    assert done["state"] == "approved"


async def test_a_scoped_role_only_gets_and_opens_its_own_teams_tickets(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    """Ravi's role works on Shipping only, but he is still listed in the Billing team."""
    w = await desk(client, app_sessionmaker)
    made = await role(client, w, reply=True, scope="team_only", team_id=w["teams"]["Shipping"])
    ravi = await give(client, w, made["id"])
    teams = f"/api/v1/teams/{w['teams']['Billing']}"
    ok(await client.patch(teams, headers=w["h"], json={"member_ids": [w["ravi"]]}))

    # The Assignee picker can tell: he takes tickets, but only Shipping's.
    listed = {a["id"]: a for a in ok(await client.get("/api/v1/agents", headers=w["h"]))}
    assert (listed[w["ravi"]]["assignable"], listed[w["ravi"]]["works_on_team_id"]) == (
        True, w["teams"]["Shipping"],
    )  # fmt: skip

    # Routing skips him for Billing, and nobody can give him a Billing ticket.
    t = await ticket(client, w, "Refund", "Billing")
    assert t["assignee_id"] is None
    r = await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"assignee_id": w["ravi"]})
    assert r.status_code == 422
    assert r.json()["error"]["message"].startswith("Ravi's role works on Shipping's tickets")
    body = {"subject": "Lost parcel", "contact": {"email": "priya@example.com", "name": "Priya"}}
    r = await client.post(
        T, headers=w["h"], json=body | {"assignee_id": w["ravi"], "team_id": w["teams"]["Billing"]}
    )
    assert r.status_code == 422

    # He opens tickets for Shipping (picked for him when no team is sent), not for others.
    mine = ok(await client.post(T, headers=ravi, json=body), 201)
    assert mine["team_id"] == w["teams"]["Shipping"]
    ok(await client.get(f"{T}/{mine['id']}", headers=ravi))
    r = await client.post(T, headers=ravi, json=body | {"team_id": w["teams"]["Billing"]})
    assert r.status_code == 422
    # A child ticket is the way to hand work to another team.
    ok(
        await client.post(
            f"{T}/{mine['id']}/children", headers=ravi, json={"team_id": w["teams"]["Billing"]}
        ),
        201,
    )
