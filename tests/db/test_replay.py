"""Replay (differentiator plan #3) through the real API with RLS on: a rule or workflow
tried on past tickets.

- a saved rule's replay counts what it would change, keeps examples, says the same as
  test mode, and changes, sends and logs nothing;
- one replay at a time per workspace; the limit is 1-1000; a replay goes page by page
  and can be cancelled; one whose rule was deleted fails and says why;
- conflicts with other enabled rules are found, with the winner by run order;
- a workflow is walked to its first wait; an older version can be replayed and restored
  (as a new version, audited);
- Admins only; another workspace sees nothing; old replays are pruned.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from cedesk.helpdesk.domain import replay as replay_rules
from cedesk.helpdesk.service import replay
from tests.db.helpers import Sm, ok
from tests.db.inbox_helpers import as_, desk, sql
from tests.db.test_automation_rules import R, new_ticket, rule
from tests.db.test_flows import F, flow, follow_up

pytestmark = pytest.mark.db

REPLAYS = "/api/v1/replays"
REFUND = [{"kind": "text_contains_any", "words": ["refund"]}]


async def finish(sm: Sm, w: dict[str, Any]) -> None:
    """Worker passes until this workspace's replay is over."""
    for _ in range(50):
        if not await replay.run_workspace(sm, w["ws"]):
            return
    raise AssertionError("the replay never finished")


async def state(client: AsyncClient, w: dict[str, Any], replay_id: str) -> dict[str, Any]:
    found: dict[str, Any] = ok(await client.get(f"{REPLAYS}/{replay_id}", headers=w["h"]))
    return found


async def tickets(client: AsyncClient, w: dict[str, Any], *subjects: str) -> list[Any]:
    return [await new_ticket(client, w, s) for s in subjects]


async def test_a_rule_replay_counts_keeps_examples_and_changes_nothing(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    # Oldest first: three refunds, then three others. The newest five: 2 refunds.
    made = await tickets(client, w, "Refund 1", "Refund 2", "Refund 3", "Hi", "Hello", "Hey")
    r = await rule(
        client, w, "created", REFUND,
        [{"kind": "set_priority", "priority": "high"}, {"kind": "add_tag", "tag": "refund"},
         {"kind": "send_email", "body": "We are on it."}],
        name="Refunds", enabled=False,
    )  # fmt: skip
    outbox_before = await sql(sm, w["ws"], "SELECT count(*) FROM hd_outbox")

    started = await client.post(f"{R}/{r['id']}/replay", headers=w["h"], json={"ticket_limit": 5})
    assert started.status_code == 202, started.text
    body = started.json()
    assert body["state"] == "queued" and body["kind"] == "rule" and body["name"] == "Refunds"
    busy = await client.post(f"{R}/{r['id']}/replay", headers=w["h"], json={})
    assert busy.status_code == 409

    await finish(sm, w)
    got = await state(client, w, body["id"])
    assert got["state"] == "done" and got["finished_at"]
    assert (got["checked"], got["matched"], got["would_change"]) == (5, 2, 2)
    assert got["conflicts"] == 0 and got["summary"] == "Would change 2 of 5 tickets."
    assert sorted(s["subject"] for s in got["samples"]) == ["Refund 2", "Refund 3"]
    sample = got["samples"][0]
    assert {"field": "priority", "before": "medium", "after": "high"} in sample["changes"]
    assert {"field": "tags", "before": None, "after": "refund"} in sample["changes"]
    # The same lines test mode says, for the same ticket.
    tried = ok(await client.post(f"{R}/{r['id']}/test", headers=w["h"],
                                 json={"ticket_id": sample["ticket_id"]}))  # fmt: skip
    assert sample["said"] == [a["would"] for a in tried["actions"] if a["would"]]

    # Nothing changed, nothing was sent, queued or logged.
    for t in made:
        now = ok(await client.get(f"/api/v1/tickets/{t['id']}", headers=w["h"]))
        assert now["priority"] == t["priority"] and now["tags"] == t["tags"]
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_email_out") == []
    assert await sql(sm, w["ws"], "SELECT 1 FROM hd_automation_run") == []
    assert await sql(sm, w["ws"], "SELECT count(*) FROM hd_outbox") == outbox_before

    # Over: another may start.
    again = await client.post(f"{R}/{r['id']}/replay", headers=w["h"], json={"ticket_limit": 1})
    assert again.status_code == 202
    await finish(sm, w)


async def test_limits_pages_and_cancel(
    client: AsyncClient, app_sessionmaker: Sm, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await tickets(client, w, "Refund a", "Refund b", "Refund c", "Refund d", "Refund e")
    draft = {"event": "created", "conditions": REFUND,
             "actions": [{"kind": "add_tag", "tag": "r"}]}  # fmt: skip
    for limit in (0, 1001):
        bad = await client.post(f"{R}/replay", headers=w["h"],
                                json={"ticket_limit": limit, "rule": draft})  # fmt: skip
        assert bad.status_code == 422
    no_rule = await client.post(f"{R}/replay", headers=w["h"], json={})
    assert no_rule.status_code == 422

    monkeypatch.setattr(replay_rules, "PAGE", 2)
    monkeypatch.setattr(replay, "PAGES_PER_PASS", 1)
    body = ok(await client.post(f"{R}/replay", headers=w["h"],
                                json={"ticket_limit": 5, "rule": draft}), 202)  # fmt: skip
    assert body["target_id"] is None and body["name"] == "Test"
    assert await replay.run_workspace(sm, w["ws"]) == 1
    part = await state(client, w, body["id"])
    assert part["state"] == "running" and part["checked"] == 2
    assert part["summary"] == "Checked 2 of up to 5 tickets so far."
    # The pass let it go: the next pass carries on from the cursor.
    assert await replay.run_workspace(sm, w["ws"]) == 1
    assert (await state(client, w, body["id"]))["checked"] == 4

    stopped = ok(await client.post(f"{REPLAYS}/{body['id']}/cancel", headers=w["h"]))
    assert stopped["state"] == "cancelled" and stopped["checked"] == 4
    assert await replay.run_workspace(sm, w["ws"]) == 0  # nothing left to do
    again = await client.post(f"{REPLAYS}/{body['id']}/cancel", headers=w["h"])
    assert again.status_code == 409


async def test_a_replay_whose_rule_was_deleted_fails_and_says_why(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await tickets(client, w, "Refund")
    r = await rule(client, w, "created", REFUND, [{"kind": "add_tag", "tag": "r"}])
    body = ok(await client.post(f"{R}/{r['id']}/replay", headers=w["h"], json={}), 202)
    assert (await client.delete(f"{R}/{r['id']}", headers=w["h"])).status_code == 204
    await finish(sm, w)
    got = await state(client, w, body["id"])
    assert got["state"] == "failed" and got["error"] == "The rule was deleted."


async def test_conflicts_name_the_other_rule_and_the_winner(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await tickets(client, w, "Refund please", "Hello")
    billing = await rule(
        client, w, "created", REFUND,
        [{"kind": "set_team", "team_id": w["teams"]["Billing"]}], name="To Billing",
    )  # fmt: skip
    to_shipping = {"kind": "set_team", "team_id": w["teams"]["Shipping"]}

    # An unsaved rule goes last, so it would win.
    draft = {"event": "created", "conditions": REFUND, "actions": [to_shipping]}
    body = ok(await client.post(f"{R}/replay", headers=w["h"], json={"rule": draft}), 202)
    await finish(sm, w)
    got = await state(client, w, body["id"])
    assert got["would_change"] == 1 and got["conflicts"] == 1
    clash = got["conflict_samples"][0]
    assert clash["field"] == "team" and clash["winner"] == "tested"
    assert (clash["tested_value"], clash["other_value"]) == ("Shipping", "Billing")
    assert clash["other_rule"] == "To Billing" and clash["other_rule_id"] == billing["id"]
    assert got["summary"] == "Would change 1 of 2 tickets, 1 conflicts with other rules."

    # Saved and moved first: "To Billing" runs later and wins.
    mine = await rule(client, w, "created", REFUND, [to_shipping], name="To Shipping")
    ok(await client.put(f"{R}/order", headers=w["h"],
                        json={"ids": [mine["id"], billing["id"]]}))  # fmt: skip
    body = ok(await client.post(f"{R}/{mine['id']}/replay", headers=w["h"], json={}), 202)
    await finish(sm, w)
    got = await state(client, w, body["id"])
    assert got["conflicts"] == 1 and got["conflict_samples"][0]["winner"] == "other"


async def test_a_workflow_replay_and_restoring_an_older_version(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await tickets(client, w, "One", "Two", "Three")
    first = follow_up()
    f = await flow(client, w, first, enabled=False)
    body = ok(await client.post(f"{F}/{f['id']}/replay", headers=w["h"], json={}), 202)
    assert body["kind"] == "flow" and body["target_version"] == 1
    await finish(sm, w)
    got = await state(client, w, body["id"])
    assert (got["checked"], got["would_change"], got["would_wait"]) == (3, 3, 3)
    assert got["samples"][0]["changes"] == [{"field": "tags", "before": None, "after": "new"}]

    # Version 2 tags "fresh"; going back to version 1 makes version 3.
    second = follow_up()
    second["nodes"][1]["action"]["tag"] = "fresh"
    changed = ok(await client.patch(f"{F}/{f['id']}", headers=w["h"], json={"graph": second}))
    assert changed["version"] == 2
    old = ok(await client.post(f"{F}/{f['id']}/replay", headers=w["h"], json={"version": 1}), 202)
    assert old["target_version"] == 1
    await finish(sm, w)
    assert (await state(client, w, old["id"]))["samples"][0]["said"] == ["Tag new"]
    missing = await client.post(f"{F}/{f['id']}/replay", headers=w["h"], json={"version": 9})
    assert missing.status_code == 404

    restored = ok(await client.post(f"{F}/{f['id']}/versions/1/restore", headers=w["h"]))
    assert restored["version"] == 3
    graphs = {v["version"]: v["graph"] for v in
              ok(await client.get(f"{F}/{f['id']}/versions", headers=w["h"]))}  # fmt: skip
    assert sorted(graphs) == [1, 2, 3] and graphs[3] == graphs[1] == restored["graph"]
    audit = await sql(sm, w["ws"], "SELECT action, detail FROM hd_audit WHERE area = 'flows' "
                      "AND action = 'restored'")  # fmt: skip
    assert len(audit) == 1 and audit[0][1]["from_version"] == 1
    assert audit[0][1]["new_version"] == 3
    same = await client.post(f"{F}/{f['id']}/versions/3/restore", headers=w["h"])
    assert same.status_code == 409
    none = await client.post(f"{F}/{f['id']}/versions/9/restore", headers=w["h"])
    assert none.status_code == 404


async def test_admins_only_isolated_and_pruned(client: AsyncClient, app_sessionmaker: Sm) -> None:
    sm = app_sessionmaker
    w = await desk(client, sm)
    await tickets(client, w, "Refund")
    r = await rule(client, w, "created", REFUND, [{"kind": "add_tag", "tag": "r"}])
    neha = await as_(client, w, "Neha")
    assert (await client.post(f"{R}/{r['id']}/replay", headers=neha, json={})).status_code == 403
    body = ok(await client.post(f"{R}/{r['id']}/replay", headers=w["h"], json={}), 202)
    assert (await client.get(f"{REPLAYS}/{body['id']}", headers=neha)).status_code == 403
    other = await desk(client, sm)
    assert (await client.get(f"{REPLAYS}/{body['id']}", headers=other["h"])).status_code == 404
    await finish(sm, w)

    await sql(sm, w["ws"], "UPDATE hd_rule_replay SET finished_at = now() - interval '31 days'")
    assert await replay.prune_everywhere(sm) >= 1
    assert (await client.get(f"{REPLAYS}/{body['id']}", headers=w["h"])).status_code == 404
