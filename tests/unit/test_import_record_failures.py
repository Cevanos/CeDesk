"""One record that can't be read must not stop an import (P0-2), with no network.

- `SourceHttp`: a 404 on a call about one record (`record=True`) is `RecordGone`; a 401/403
  never is (bad credentials stop the run); a 5xx carries its Retry-After.
- `RetryPolicy`: bounded attempts, exponential backoff with jitter, Retry-After respected.
- `RecordFailures`: a gone record is noted and skipped; a temporary error is raised again
  (the page is retried) unless the run is isolating records after its retries ran out.
- Each importer that reads a ticket's messages one ticket at a time (Freshdesk, Help Scout,
  Zoho, Gorgias, Intercom): a ticket deleted after it was listed is left out and noted, and
  the rest of the page is imported.
"""

import asyncio
from typing import Any

import httpx
import pytest

from cedesk.helpdesk.domain import imports as rules
from cedesk.helpdesk.domain.imports import RateLimited, RecordFailures, RecordGone, SourceError
from cedesk.helpdesk.service.imports.http import SourceHttp
from tests.unit import test_import_freshdesk as fdt
from tests.unit import test_import_gorgias as got
from tests.unit import test_import_helpscout as hst
from tests.unit import test_import_intercom as ict
from tests.unit import test_import_zendesk as zdt
from tests.unit import test_import_zoho as zot


async def allow(host: str) -> None:
    return None


def http(handler: Any) -> SourceHttp:
    return SourceHttp(
        "acme.example.com", headers={}, guard=allow, transport=httpx.MockTransport(handler)
    )


def answer(status: int, **headers: str) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={}, headers=headers)

    return handle


# The HTTP layer


def test_a_404_about_one_record_is_record_gone() -> None:
    with pytest.raises(RecordGone) as caught:
        asyncio.run(http(answer(404)).get("/tickets/7/comments", record=True))
    assert caught.value.permanent and "token" not in str(caught.value).lower()


def test_a_404_on_a_list_is_still_a_failure_of_the_run() -> None:
    with pytest.raises(SourceError) as caught:
        asyncio.run(http(answer(404)).get("/tickets"))
    assert caught.value.permanent and not isinstance(caught.value, RecordGone)


@pytest.mark.parametrize("status", [401, 403])
def test_bad_credentials_are_never_a_gone_record(status: int) -> None:
    with pytest.raises(SourceError) as caught:
        asyncio.run(http(answer(status)).get("/tickets/7/comments", record=True))
    assert caught.value.permanent and not isinstance(caught.value, RecordGone)


def test_a_5xx_carries_its_retry_after() -> None:
    with pytest.raises(SourceError) as caught:
        asyncio.run(http(answer(503, **{"Retry-After": "120"})).get("/tickets"))
    assert not caught.value.permanent and caught.value.retry_after == 120


# The retry policy


def test_backoff_doubles_with_jitter_and_stops_at_the_cap() -> None:
    policy = rules.RetryPolicy(attempts=8, base_seconds=30, max_seconds=3600)
    low = [policy.delay(n, None, rand=lambda: 0.0) for n in range(1, 10)]
    high = [policy.delay(n, None, rand=lambda: 1.0) for n in range(1, 10)]
    assert high[:4] == [30, 60, 120, 240] and high[-1] == 3600  # doubles, then capped
    assert low[:4] == [15, 30, 60, 120]  # jitter keeps at least half the wait
    assert all(lo <= hi for lo, hi in zip(low, high, strict=True))


def test_backoff_respects_retry_after_but_never_waits_forever() -> None:
    policy = rules.RetryPolicy(attempts=8, base_seconds=30, max_seconds=3600)
    assert policy.delay(1, 600, rand=lambda: 1.0) == 600
    assert policy.delay(1, 10**6, rand=lambda: 1.0) == rules.MAX_RATE_WAIT


@pytest.mark.parametrize("bad", [0, -1])
def test_a_policy_needs_at_least_one_attempt(bad: int) -> None:
    with pytest.raises(ValueError):
        rules.RetryPolicy(attempts=bad)


# Noting failed records


async def gone() -> Any:
    raise RecordGone("The source no longer has this record (404).")


async def down() -> Any:
    raise SourceError("The source answered 503; will retry.", permanent=False)


async def refused() -> Any:
    raise SourceError("The source refused these credentials.", permanent=True)


async def limited() -> Any:
    raise RateLimited(30)


def test_a_gone_record_is_noted_and_skipped() -> None:
    failures = RecordFailures()
    assert asyncio.run(failures.guard("ticket", "7", gone)) is None
    [noted] = failures.take()
    assert (noted.kind, noted.external_id, noted.category) == ("ticket", "7", "not_found")
    assert failures.take() == []  # taken once


def test_a_temporary_error_is_raised_again_until_the_run_isolates() -> None:
    failures = RecordFailures()
    with pytest.raises(SourceError):
        asyncio.run(failures.guard("ticket", "7", down))
    assert failures.take() == []
    failures.isolate = True
    assert asyncio.run(failures.guard("ticket", "7", down)) is None
    [noted] = failures.take()
    assert noted.category == "temporary" and "503" in noted.error


def test_credentials_and_rate_limits_are_never_noted_as_a_record() -> None:
    failures = RecordFailures(isolate=True)
    with pytest.raises(SourceError):
        asyncio.run(failures.guard("ticket", "7", refused))
    with pytest.raises(RateLimited):
        asyncio.run(failures.guard("ticket", "7", limited))
    assert failures.take() == []


# Each importer: a ticket deleted after it was listed


def freshdesk_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": i,
            "subject": f"T{i}",
            "status": 2,
            "requester": {"id": 1, "name": "A"},
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": f"2025-01-01T00:0{i}:00Z",
        }
        for i in (1, 2, 3)
    ]


async def test_freshdesk_a_ticket_deleted_during_the_import_is_left_out_and_noted() -> None:
    def gone_route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"description": "not found"})

    desk = fdt.Desk(
        fdt.base_routes()
        | {
            "/api/v2/tickets": fdt.ticket_list(freshdesk_rows()),
            "/api/v2/tickets/3/conversations": [],
            "/api/v2/tickets/2/conversations": gone_route,
        }
    )
    connector = fdt.connect(desk, archived=False)
    page = await connector.page("tickets", None)
    assert [t.external_id for t in page.items] == ["1", "3"]
    [noted] = connector.failures.take()
    assert (noted.kind, noted.external_id, noted.category) == ("ticket", "2", "not_found")


async def test_freshdesk_refused_credentials_still_stop_the_run() -> None:
    def refused_route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    desk = fdt.Desk(
        fdt.base_routes()
        | {
            "/api/v2/tickets": fdt.ticket_list(freshdesk_rows()),
            "/api/v2/tickets/2/conversations": refused_route,
        }
    )
    connector = fdt.connect(desk, archived=False)
    with pytest.raises(SourceError) as caught:
        await connector.page("tickets", None)
    assert caught.value.permanent and not isinstance(caught.value, RecordGone)
    assert connector.failures.take() == []


async def test_freshdesk_a_ticket_that_keeps_failing_is_isolated_only_when_asked() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    desk = fdt.Desk(
        fdt.base_routes()
        | {
            "/api/v2/tickets": fdt.ticket_list(freshdesk_rows()),
            "/api/v2/tickets/3/conversations": [],
            "/api/v2/tickets/2/conversations": broken,
        }
    )
    connector = fdt.connect(desk, archived=False)
    with pytest.raises(SourceError):  # retries are not used up: the page is tried again
        await connector.page("tickets", None)
    connector.failures.take()
    connector.failures.isolate = True
    page = await connector.page("tickets", None)
    assert [t.external_id for t in page.items] == ["1", "3"]
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("2", "temporary")


class HelpScoutGone(hst.Fake):
    """Help Scout, where conversation 1001's threads are gone (deleted after listing)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/conversations/1001/threads":
            self.requests.append(request)
            return httpx.Response(404, json={})
        return super().__call__(request)


def test_helpscout_a_conversation_whose_threads_are_gone_is_left_out_and_noted() -> None:
    fake = HelpScoutGone()
    connector = hst.connector(fake)
    page = hst.run(connector.page("tickets", None))
    assert page.items == [] and page.cursor == {"page": 2}  # 1001 was the page's only one
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("1001", "not_found")
    assert fake.tokens == 1  # a gone record is not mistaken for an expired token


def test_zoho_a_ticket_whose_threads_are_gone_is_left_out_and_noted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/z1/threads":
            return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})
        return zot.route(request)

    connector = zot.connector(handler)
    page = zot.run(connector.page("tickets", None))
    assert [t.external_id for t in page.items] == ["z2"]
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("z1", "not_found")


def test_gorgias_a_ticket_whose_messages_are_gone_is_left_out_and_noted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/messages" and request.url.params.get("ticket_id") == "100":
            return httpx.Response(404, json={})
        return got.route(request)

    connector = got.connector(handler)
    page = got.run(connector.page("tickets", None))
    ids = [t.external_id for t in page.items]
    assert "100" not in ids and ids
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("100", "not_found")


class IntercomGone(ict.Mock):
    """Intercom, where conversation 502 was deleted after the list was read."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/conversations/502":
            self.requests.append(request)
            return httpx.Response(404, json={})
        return super().__call__(request)


def test_intercom_a_conversation_deleted_after_listing_is_noted_not_dropped() -> None:
    connector: Any = ict.connector(IntercomGone())
    page = asyncio.run(connector.page("tickets", None))
    assert [t.external_id for t in page.items] == ["501"]
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("502", "not_found")


# A ticket imported without all its history is noted, never passed off as complete


def test_zendesk_comments_404_keeps_the_ticket_and_notes_its_missing_history() -> None:
    connector: Any = zdt.connector(zdt.Desk())
    page = zdt.run(connector.page("tickets", None))
    old = next(t for t in page.items if t.external_id == "103")  # comments answer 404
    assert [(m.kind, m.body) for m in old.messages] == [("customer", "Archived question")]
    [noted] = connector.failures.take()
    assert (noted.kind, noted.external_id, noted.category) == ("ticket", "103", "incomplete")
    assert "description" in noted.error and "404" in noted.error


def test_zendesk_comments_refused_still_stop_the_run() -> None:
    def refused(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/api/v2/tickets/103/comments.json":
            return httpx.Response(403, json={})
        return None

    connector: Any = zdt.connector(zdt.Desk(refused))
    with pytest.raises(SourceError) as caught:
        zdt.run(connector.page("tickets", None))
    assert caught.value.permanent and not isinstance(caught.value, RecordGone)
    assert connector.failures.take() == []


def test_zoho_a_thread_gone_mid_read_keeps_its_summary_and_notes_the_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tickets/z1/threads/t1":
            return httpx.Response(404, json={"errorCode": "URL_NOT_FOUND"})
        return zot.route(request)

    connector = zot.connector(handler)
    page = zot.run(connector.page("tickets", None))
    assert [t.external_id for t in page.items] == ["z1", "z2"]
    [noted] = connector.failures.take()
    assert (noted.external_id, noted.category) == ("z1", "incomplete")


def test_an_incomplete_record_is_kept_and_noted() -> None:
    failures = RecordFailures()
    failures.incomplete("ticket", "7", "Only part of it was read.")
    [noted] = failures.take()
    assert (noted.external_id, noted.category) == ("7", "incomplete")
