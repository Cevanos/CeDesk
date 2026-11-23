"""Workflows (feature-gap plan 5.2, 5.3, 5.5) through the real API with RLS on: saving
checks the graph and what it names; each graph change is a new version; the catalog; a
flow runs on its trigger after the rules, waits, and the worker moves it on (by time, by
the customer's reply, or by the timeout) exactly once; turning it off, deleting it or
resolving the ticket cancels waiting runs; test mode; the audit log; who may manage
workflows; another workspace sees none of it; privacy export and erase cover runs."""

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.service.automation import resume_due
from cedesk.storage import ObjectStore
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import T, as_, audit, desk, sql
from tests.db.test_automation_rules import detail, new_ticket, reply, runs
from tests.db.test_custom_roles import give, role
from tests.db.test_privacy import ANA, P, app_parts, client, customer_desk, store
from tests.db.test_webhooks import install

pytestmark = pytest.mark.db
__all__ = ["app_parts", "client", "store"]  # fixtures

F = "/api/v1/flows"
RUNS = "/api/v1/automation/runs"


def follow_up(minutes: int = 60, *, until_reply: bool = True) -> dict[str, Any]:
    """New ticket → tag "new" → wait (for the reply) → replied: tag "answered";
    timeout: priority High."""
    wait: dict[str, Any] = {"id": "wait", "type": "wait", "minutes": minutes}
    if until_reply:
        wait["until"] = "customer_replied"
    nodes = [
        {"id": "start", "type": "trigger", "event": "created", "position": {"x": 0, "y": 0}},
        {
            "id": "tag",
            "type": "action",
            "action": {"kind": "add_tag", "tag": "new"},
            "label": "Tag it",
        },
        wait,
        {"id": "answered", "type": "action", "action": {"kind": "add_tag", "tag": "answered"}},
        {"id": "chase", "type": "action", "action": {"kind": "set_priority", "priority": "high"}},
    ]
    edges = [{"from": "start", "to": "tag"}, {"from": "tag", "to": "wait"}]
    if until_reply:
        edges += [
            {"from": "wait", "to": "answered", "when": "replied"},
            {"from": "wait", "to": "chase", "when": "timeout"},
        ]
    else:
        nodes = nodes[:4]
        edges += [{"from": "wait", "to": "answered"}]
    return {"nodes": nodes, "edges": edges}


async def flow(
    client: AsyncClient,
    w: dict[str, Any],
    graph: dict[str, Any],
    *,
    name: str = "Follow up",
    enabled: bool = True,
    status: int = 201,
    **extra: Any,
) -> Any:
    r = await client.post(
        F, headers=w["h"], json={"name": name, "graph": graph, "enabled": enabled} | extra
    )
    assert r.status_code == status, r.text
    return r.json()


async def due_now(sm: Sm, w: dict[str, Any]) -> None:
    await sql(
        sm,
        w["ws"],
        "UPDATE hd_automation_run SET resume_at = now() - interval '1 second' "
        "WHERE state = 'waiting'",
    )


async def test_saving_checks_the_graph(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    good = follow_up()
    trigger = good["nodes"][0]
    bad: list[Any] = [
        {"nodes": [trigger], "edges": []},  # too few nodes
        {"nodes": good["nodes"], "edges": good["edges"], "extra": 1},
        {"nodes": [*good["nodes"], {**trigger, "id": "start2"}], "edges": good["edges"]},
        {"nodes": good["nodes"], "edges": good["edges"][:2]},  # answered, chase unreachable
        {"nodes": good["nodes"], "edges": [*good["edges"], {"from": "tag", "to": "start"}]},
        {"nodes": good["nodes"], "edges": [*good["edges"], {"from": "tag", "to": "chase"}]},
        {
            "nodes": [*good["nodes"][:2], {"id": "x", "type": "teleport"}],
            "edges": [{"from": "start", "to": "tag"}, {"from": "tag", "to": "x"}],
        },
        {
            "nodes": [
                trigger,
                {"id": "a", "type": "action", "action": {"kind": "add_tag", "tag": "a"}},
                {"id": "b", "type": "action", "action": {"kind": "add_tag", "tag": "b"}},
            ],
            "edges": [
                {"from": "start", "to": "a"},
                {"from": "a", "to": "b"},
                {"from": "b", "to": "a"},
            ],
        },  # a loop without a wait
        {
            "nodes": [trigger, {"id": "w", "type": "wait", "minutes": 99999}],
            "edges": [{"from": "start", "to": "w"}],
        },
        {
            "nodes": [
                trigger,
                {
                    "id": "c",
                    "type": "condition",
                    "conditions": [{"kind": "sla_in", "states": ["breached"]}],
                },
            ],
            "edges": [{"from": "start", "to": "c", "when": "next"}],
        },
    ]
    for graph in bad:
        r = await client.post(f"{F}/validate", headers=w["h"], json={"graph": graph})
        assert ok(r)["valid"] is False and ok(r)["error"], graph
        await flow(client, w, graph, status=422)
    assert ok(await client.post(f"{F}/validate", headers=w["h"], json={"graph": good})) == {
        "valid": True,
        "trigger": "created",
        "error": None,
    }
    # What it names must exist.
    missing = {
        "nodes": [
            trigger,
            {
                "id": "f",
                "type": "action",
                "action": {"kind": "add_follower", "member_id": str(UUID(int=4))},
            },
        ],
        "edges": [{"from": "start", "to": "f"}],
    }
    assert ok(await client.post(f"{F}/validate", headers=w["h"], json={"graph": missing}))["valid"]
    await flow(client, w, missing, status=422)
    # A loop through a wait is fine.
    looped = {
        "nodes": [
            trigger,
            {"id": "w", "type": "wait", "minutes": 60},
            {"id": "t", "type": "action", "action": {"kind": "add_tag", "tag": "x"}},
        ],
        "edges": [{"from": "start", "to": "t"}, {"from": "t", "to": "w"}, {"from": "w", "to": "t"}],
    }
    made = await flow(client, w, looped, name="Every hour")
    assert made["enabled"] is True and made["version"] == 1 and made["problems"] == []
    assert all("when" in e for e in made["graph"]["edges"])
    await flow(client, w, good, name="every HOUR", status=409)
    off = ok(await client.post(F, headers=w["h"], json={"name": "Off", "graph": good}), 201)
    assert off["enabled"] is False  # a workflow starts turned off


async def test_versions_audit_and_who_may_manage(client: AsyncClient, app_sessionmaker: Sm) -> None:
    install(client)
    w = await desk(client, app_sessionmaker)
    made = await flow(client, w, follow_up(), enabled=False)
    fid = made["id"]
    # Renaming or limits don't make a version; a new graph does.
    ok(await client.patch(f"{F}/{fid}", headers=w["h"], json={"name": "Chase up"}))
    changed = ok(await client.patch(f"{F}/{fid}", headers=w["h"], json={"graph": follow_up(30)}))
    assert changed["version"] == 2 and changed["name"] == "Chase up"
    same = ok(await client.patch(f"{F}/{fid}", headers=w["h"], json={"graph": follow_up(30)}))
    assert same["version"] == 2
    ok(await client.patch(f"{F}/{fid}", headers=w["h"], json={"enabled": True}))
    bad = await client.patch(
        f"{F}/{fid}", headers=w["h"], json={"max_runs_per_hour": 500, "max_runs_per_day": 100}
    )
    assert bad.status_code == 422
    assert (
        await client.patch(f"{F}/{fid}", headers=w["h"], json={"name": None})
    ).status_code == 422
    found = ok(await client.get(f"{F}/{fid}/versions", headers=w["h"]))
    assert [v["version"] for v in found] == [2, 1]
    assert found[1]["graph"]["nodes"][2]["minutes"] == 60
    assert [f["name"] for f in ok(await client.get(F, headers=w["h"]))] == ["Chase up"]
    entries = await audit(client, w, "flows")
    assert [e["action"] for e in reversed(entries)] == ["created", "changed", "changed", "enabled"]
    assert (await client.delete(f"{F}/{fid}", headers=w["h"])).status_code == 204
    assert (await audit(client, w, "flows"))[0]["action"] == "deleted"
    assert (await client.get(f"{F}/{fid}", headers=w["h"])).status_code == 404

    # No settings right: nothing; the settings right: all but a webhook workflow.
    for who in ("Neha", "Sam", "Meena"):
        h = await as_(client, w, who)
        for r in (
            await client.get(F, headers=h),
            await client.get("/api/v1/automation/catalog", headers=h),
            await client.post(F, headers=h, json={"name": "X", "graph": follow_up()}),
            await client.post(f"{F}/validate", headers=h, json={"graph": follow_up()}),
        ):
            assert r.status_code == 403
    ravi = await give(
        client, {**w, "ravi": w["ids"]["Ravi"]}, (await role(client, w, settings=True))["id"]
    )
    mine = ok(
        await client.post(F, headers=ravi, json={"name": "Ravi's", "graph": follow_up()}), 201
    )
    hook = ok(
        await client.post(
            "/api/v1/webhooks",
            headers=w["h"],
            json={"url": "https://hooks.example.com/x", "events": ["ticket.resolved"]},
        ),
        201,
    )
    calls = {
        "nodes": [
            follow_up()["nodes"][0],
            {
                "id": "h",
                "type": "action",
                "action": {"kind": "call_webhook", "webhook_id": hook["id"]},
            },
        ],
        "edges": [{"from": "start", "to": "h"}],
    }
    r = await client.patch(f"{F}/{mine['id']}", headers=ravi, json={"graph": calls})
    assert r.status_code == 403
    ok(await client.patch(f"{F}/{mine['id']}", headers=w["h"], json={"graph": calls}))
    r = await client.patch(f"{F}/{mine['id']}", headers=ravi, json={"enabled": False})
    assert r.status_code == 403  # an Admin's workflow now


async def test_the_catalog(client: AsyncClient, app_sessionmaker: Sm) -> None:
    w = await desk(client, app_sessionmaker)
    got = ok(await client.get("/api/v1/automation/catalog", headers=w["h"]))
    assert [t["event"] for t in got["triggers"]] == [
        "created",
        "customer_replied",
        "sla_breached",
        "snooze_ended",
    ]
    kinds = {c["kind"] for c in got["conditions"]}
    assert {"contact_field", "contact_in_segment", "time_since", "snoozed"} <= kinds
    actions = {a["kind"]: a for a in got["actions"]}
    assert {"snooze", "add_follower", "send_email", "call_webhook"} <= set(actions)
    assert actions["snooze"]["params"]["properties"]["minutes"]["maximum"] > 0
    assert {n["type"] for n in got["nodes"]} == {"trigger", "condition", "action", "wait"}
    assert got["limits"]["nodes"] == 50


async def test_a_wait_until_reply_moves_on_by_the_reply_once(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await client.post(
        "/api/v1/rules",
        headers=w["h"],
        json={
            "name": "First",
            "event": "created",
            "conditions": [{"kind": "status_in", "statuses": ["open"]}],
            "actions": [{"kind": "add_tag", "tag": "rule"}],
        },
    )
    made = await flow(client, w, follow_up())
    t = await new_ticket(client, w)
    assert sorted(t["tags"]) == ["new", "rule"]  # the rule first, then the flow; one save
    [run] = await runs(client, w, f"?flow_id={made['id']}")
    assert (run["kind"], run["state"], run["node_id"], run["until_reply"]) == (
        "flow",
        "waiting",
        "wait",
        True,
    )
    assert [(s["node"], s["outcome"]) for s in run["steps"]] == [
        ("tag", "done"),
        ("wait", "waiting"),
    ]
    assert ok(await client.get(f"{F}/{made['id']}", headers=w["h"]))["waiting_runs"] == 1
    assert await resume_due(sm) == 0  # not due yet

    await reply(sm, w, t["id"])
    assert await resume_due(sm) == 1
    assert await resume_due(sm) == 0  # resumes once
    got = await detail(client, w, t["id"])
    assert sorted(got["tags"]) == ["answered", "new", "rule"] and got["priority"] != "high"
    done = ok(await client.get(f"{RUNS}/{run['id']}", headers=w["h"]))
    assert done["state"] == "done" and done["finished_at"] and done["node_id"] is None
    assert ("wait", "replied") in [(s["node"], s["outcome"]) for s in done["steps"]]
    assert any(
        b.startswith("Workflow Follow up → Tag answered")
        for b in [m["body"] for m in got["messages"] if m["kind"] == "system"]
    )


async def test_a_wait_times_out_or_passes(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await flow(client, w, follow_up())
    await flow(client, w, follow_up(until_reply=False), name="Later")
    t = await new_ticket(client, w)
    await due_now(sm, w)
    # Two workers at once: each run moves on once.
    moved = await asyncio.gather(resume_due(sm), resume_due(sm))
    assert sum(moved) == 2
    got = await detail(client, w, t["id"])
    assert got["priority"] == "high" and sorted(got["tags"]) == ["answered", "new"]
    outcomes = {
        r["name"]: [s["outcome"] for s in r["steps"] if s["type"] == "wait"]
        for r in await runs(client, w)
    }
    assert outcomes == {"Follow up": ["waiting", "timeout"], "Later": ["waiting", "next"]}


async def test_off_deleted_resolved_or_stopped_cancels_waiting_runs(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    a = await flow(client, w, follow_up(), name="A")
    b = await flow(client, w, follow_up(), name="B")
    c = await flow(client, w, follow_up(), name="C")
    d = await flow(client, w, follow_up(), name="D")
    t = await new_ticket(client, w)
    t2 = await new_ticket(client, w, "Other")
    ok(await client.patch(f"{F}/{a['id']}", headers=w["h"], json={"enabled": False}))
    assert (await client.delete(f"{F}/{b['id']}", headers=w["h"])).status_code == 204
    states = {
        r["name"]: (r["state"], r["result"]) for r in await runs(client, w, f"?ticket_id={t['id']}")
    }
    assert states["A"] == ("cancelled", "The workflow was turned off.")
    assert states["B"] == ("cancelled", "The workflow was deleted.")
    # Stopping one by hand (audited); only a waiting one.
    [c_run] = await runs(client, w, f"?flow_id={c['id']}&ticket_id={t['id']}")
    stopped = ok(await client.post(f"{RUNS}/{c_run['id']}/cancel", headers=w["h"]))
    assert stopped["state"] == "cancelled"
    again = await client.post(f"{RUNS}/{c_run['id']}/cancel", headers=w["h"])
    assert again.status_code == 409
    assert (await audit(client, w, "flows"))[0]["action"] == "run_cancelled"
    # Resolving the ticket: D's run there is cancelled when it comes due; the other goes on.
    ok(await client.patch(f"{T}/{t['id']}", headers=w["h"], json={"status": "resolved"}))
    await due_now(sm, w)
    await resume_due(sm)
    [d_run] = await runs(client, w, f"?flow_id={d['id']}&ticket_id={t['id']}")
    assert (d_run["state"], d_run["result"]) == ("cancelled", "The ticket was resolved.")
    assert (await detail(client, w, t["id"]))["priority"] != "high"
    assert (await detail(client, w, t2["id"]))["priority"] == "high"
    assert await runs(client, w, "?state=waiting") == []


async def test_test_mode_walks_without_changing_anything(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    t = await new_ticket(client, w)
    graph = follow_up()
    graph["nodes"].insert(
        1,
        {"id": "vip", "type": "condition", "conditions": [{"kind": "contact_vip", "value": True}]},
    )
    graph["edges"] = [
        {"from": "start", "to": "vip"},
        {"from": "vip", "to": "tag", "when": "no"},
        *graph["edges"][1:],
    ]
    got = ok(
        await client.post(f"{F}/test", headers=w["h"], json={"ticket_id": t["id"], "graph": graph})
    )
    assert [(s["node"], s["outcome"]) for s in got["steps"]] == [
        ("vip", "no"),
        ("tag", "done"),
        ("wait", "waiting"),
    ]
    assert got["would_wait"]["minutes"] == 60 and got["would_wait"]["until_reply"] is True
    assert got["ticket_after"]["tags"] == ["new"] and got["blocked"] is None
    made = await flow(client, w, graph, enabled=False)
    saved = ok(
        await client.post(f"{F}/{made['id']}/test", headers=w["h"], json={"ticket_id": t["id"]})
    )
    assert saved["steps"] == got["steps"]
    assert (
        await client.post(f"{F}/test", headers=w["h"], json={"ticket_id": t["id"]})
    ).status_code == 422
    assert (await detail(client, w, t["id"]))["tags"] == []
    assert await runs(client, w) == []


async def test_another_workspace_sees_no_workflows(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    made = await flow(client, w, follow_up())
    t = await new_ticket(client, w)
    [run] = await runs(client, w)
    other = await desk(client, sm)
    h = other["h"]
    assert ok(await client.get(F, headers=h)) == []
    for r in (
        await client.get(f"{F}/{made['id']}", headers=h),
        await client.patch(f"{F}/{made['id']}", headers=h, json={"enabled": False}),
        await client.delete(f"{F}/{made['id']}", headers=h),
        await client.get(f"{F}/{made['id']}/versions", headers=h),
        await client.post(f"{F}/{made['id']}/test", headers=h, json={"ticket_id": t["id"]}),
        await client.post(
            f"{F}/test", headers=h, json={"ticket_id": t["id"], "graph": follow_up()}
        ),
        await client.get(f"{RUNS}/{run['id']}", headers=h),
        await client.post(f"{RUNS}/{run['id']}/cancel", headers=h),
    ):
        assert r.status_code == 404, r.text
    assert ok(await client.get(f"{RUNS}?flow_id={made['id']}", headers=h))["runs"] == []
    # Their own flow with the same name is fine; their worker pass leaves ours waiting.
    await flow(client, other, follow_up())
    assert ok(await client.get(f"{F}/{made['id']}", headers=w["h"]))["waiting_runs"] == 1


async def test_privacy_export_and_erase_cover_runs(
    client: AsyncClient, app_sessionmaker: Sm, store: ObjectStore
) -> None:
    sm = app_sessionmaker
    w = await customer_desk(client, sm)
    h = w["admin"]
    w2 = {"h": h, "ws": w["ws"]}
    await flow(client, w2, follow_up())
    t = await new_ticket(client, w2, "Second", email=ANA)
    assert t["contact"]["id"] == w["contact"]
    made = ok(await client.post(f"{P}/export", headers=h, json={"email": ANA}), 201)
    data = json.loads((await client.get(f"{P}/requests/{made['id']}/file", headers=h)).content)
    [run] = data["automation_runs"]
    assert (run["automation"], run["state"], run["trigger"]) == ("Follow up", "waiting", "created")
    done = ok(
        await client.post(
            f"{P}/erase", headers=h, json={"contact_id": w["contact"], "confirm_email": ANA}
        ),
        201,
    )
    assert done["counts"]["automation_runs_cancelled"] == 1
    rows = await sql(sm, w["ws"], "SELECT state, result FROM hd_automation_run")
    assert [tuple(r) for r in rows] == [("cancelled", "The customer's data was erased.")]
