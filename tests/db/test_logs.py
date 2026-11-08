"""Structured logs (md 2B): each request line carries the request, workspace and ticket
ids, and never message bodies, search text or tokens."""

import io
import json
import logging
from typing import Any

import pytest
from httpx import AsyncClient

from cedesk import logs
from tests.db.helpers import Sm, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db

T = "/api/v1/tickets"


@pytest.fixture
def lines() -> Any:
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logs.JsonFormatter())
    logger = logging.getLogger("cedesk")
    old = (logger.handlers, logger.level, logger.propagate)
    logger.handlers, logger.propagate = [handler], False
    logger.setLevel(logging.INFO)
    yield lambda: [json.loads(x) for x in out.getvalue().splitlines()]
    logger.handlers, logger.level, logger.propagate = old


async def test_request_lines_carry_ids_not_content(
    client: AsyncClient, app_sessionmaker: Sm, lines: Any
) -> None:
    token = await sign_in(client, await new_workspace(app_sessionmaker), "admin@example.com")
    h = bearer(token)
    new = {
        "subject": "Secret subject",
        "contact": {"email": "priya@example.com", "name": "Priya"},
        "description": "card 4111",
    }
    t = ok(await client.post(T, headers=h, json=new), 201)
    ok(await client.post(f"{T}/{t['id']}/messages", headers=h | {"X-Request-ID": "trace-abc-123"},
                         json={"kind": "note", "body": "private words"}), 201)  # fmt: skip
    ok(await client.get(f"{T}?q=priya@example.com", headers=h))

    requests = [x for x in lines() if x["msg"] == "request"]
    create, note, search = requests[-3:]
    workspace_id = token.split(".")[0]
    assert create["route"] == "/api/v1/tickets" and create["status"] == 201
    assert create["workspace_id"] == workspace_id and create["method"] == "POST"
    assert note["route"] == "/api/v1/tickets/{ticket_id}/messages"
    assert note["ticket_id"] == t["id"] and note["request_id"] == "trace-abc-123"
    assert isinstance(note["ms"], float) and note["level"] == "info"
    assert search["route"] == "/api/v1/tickets" and "ticket_id" not in search
    everything = "\n".join(json.dumps(x) for x in lines())
    for secret in ("Secret subject", "4111", "private words", "priya@", token):
        assert secret not in everything


async def test_request_id_is_returned_and_free_text_is_replaced(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"
    r = await client.get("/healthz", headers={"X-Request-ID": "has spaces & <tags>"})
    assert r.headers["X-Request-ID"] != "has spaces & <tags>"
    assert len(r.headers["X-Request-ID"]) == 32


def test_only_known_keys_reach_a_line() -> None:
    record = logging.LogRecord("cedesk", logging.INFO, __file__, 1, "x", None, None)
    record.body = "customer text"  # an `extra=` the formatter must drop
    record.ticket_id = "t-1"
    line = json.loads(logs.JsonFormatter().format(record))
    assert "body" not in line and line["ticket_id"] == "t-1"
