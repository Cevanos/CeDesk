"""Which failed imports may be resumed, and what the history keeps about it (no database).

The database tests (`tests/db/test_import_resume.py`) run the whole resume; these pin the
rules it is made of: a run that isn't failed is refused with a clear reason; a connection
whose credentials are gone or can't be read is refused without saying anything about them;
the `resumed` event keeps where the run carries on from, never the source's raw cursor.
"""

from typing import Any
from uuid import uuid4

import pytest

from cedesk.crypto import Sealer
from cedesk.helpdesk.service.errors import Conflict
from cedesk.helpdesk.service.imports import framework

KEY = "k" * 64


def importers() -> framework.Importers:
    return framework.make_importers(KEY, store=None)  # type: ignore[arg-type]


def conn(**over: Any) -> dict[str, Any]:
    row = {"id": uuid4(), "source": "zendesk", "state": "connected", "settings": {},
           "credentials_sealed": None}  # fmt: skip
    return {**row, **over}


@pytest.mark.parametrize(
    ("state", "sync_on", "words"),
    [
        ("queued", False, "in progress"),
        ("running", True, "in progress"),
        ("done", False, "finished"),
        ("done", True, "syncing"),
    ],
)
def test_only_a_failed_run_is_resumable(state: str, sync_on: bool, words: str) -> None:
    with pytest.raises(Conflict, match=words):
        framework._resumable({"state": state, "sync_on": sync_on})
    framework._resumable({"state": "failed", "sync_on": sync_on})  # no error


def test_readable_credentials_pass() -> None:
    ws, row = uuid4(), conn()
    sealed = Sealer(KEY).seal("tok-secret-1234", bound_to=framework._bound(ws, row["id"]))
    framework._credentials_kept(importers(), ws, {**row, "credentials_sealed": sealed})


@pytest.mark.parametrize(
    "row",
    [
        None,  # the connection is gone
        conn(state="closed"),
        conn(credentials_sealed=None),
        conn(source="csv", settings={}),  # its file was deleted
    ],
)
def test_a_connection_without_its_credentials_is_refused(row: Any) -> None:
    with pytest.raises(Conflict, match="closed and its credentials deleted"):
        framework._credentials_kept(importers(), uuid4(), row)


def test_credentials_sealed_for_another_connection_are_refused_and_never_echoed() -> None:
    ws = uuid4()
    other = Sealer(KEY).seal("tok-secret-1234", bound_to=framework._bound(ws, uuid4()))
    with pytest.raises(Conflict, match="can't be read") as caught:
        framework._credentials_kept(importers(), ws, conn(credentials_sealed=other))
    assert "tok-secret" not in str(caught.value)


def test_a_csv_with_its_file_needs_no_credentials() -> None:
    framework._credentials_kept(
        importers(), uuid4(), conn(source="csv", settings={"file_key": "imports/a.csv"})
    )


def test_the_event_keeps_where_the_run_goes_on_never_the_raw_cursor() -> None:
    row = {
        "cursor": {"done": ["contacts"], "page": {"cursor": "opaque-source-token"}},
        "counts": {"contacts": {"created": 40, "skipped": 2}, "tickets": {"created": 15},
                   "note": "not a count"},
        "last_error": "Zendesk kept failing after 9 tries (503).",
    }  # fmt: skip
    detail = framework._resume_detail(row)
    assert detail == {
        "kinds_done": ["contacts"],
        "page_saved": True,
        "created": {"contacts": 40, "tickets": 15},
        "error": "Zendesk kept failing after 9 tries (503).",
    }
    assert "opaque-source-token" not in str(detail)


def test_the_event_of_a_run_that_saved_nothing_yet() -> None:
    detail = framework._resume_detail({"cursor": {}, "counts": {}, "last_error": None})
    assert detail == {"kinds_done": [], "page_saved": False, "created": {}, "error": ""}
