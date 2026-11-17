"""Imports from other help desks (md M1-M8, section 8). Admin only (Settings).

The flow: connect (or upload a CSV file) -> discover (counts, groups and the team each
probably maps to) -> dry run (saves nothing) -> run. A run is done by the worker, in the
background, in slices; poll `GET /imports/runs/{id}` for its state and counts. It can be
started again after it fails: records already imported are skipped, so nothing is made twice.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cedesk.helpdesk.api.deps import (
    CallerDep,
    IdempotencyKey,
    SessionmakerDep,
    idempotency,
    mark_replayed,
)
from cedesk.helpdesk.service.errors import TooLarge
from cedesk.helpdesk.service.imports import framework
from cedesk.helpdesk.service.imports.csv_source import MAX_BYTES

router = APIRouter(tags=["imports"])

Source = Literal["intercom", "zendesk", "freshdesk", "gorgias", "helpscout", "hubspot", "zoho"]
Kind = Literal["contacts", "tickets", "articles", "replies"]
Cursor = Annotated[
    str | None, Query(max_length=200, description="`next_cursor` from the previous page.")
]
Limit = Annotated[int, Query(ge=1, le=200)]


def _importers(request: Request) -> framework.Importers:
    found: framework.Importers = request.app.state.importers
    return found


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    settings: dict[str, str] = Field(
        default_factory=dict,
        description="What isn't secret: a subdomain, a region. Which keys depends on the source.",
    )
    credentials: dict[str, SecretStr] = Field(
        description="The token or key for the source. Read-only use: nothing is ever written "
        "back. Stored encrypted; deleted when the run ends without syncing."
    )


class ConnectionOut(BaseModel):
    id: UUID
    source: str
    label: str = Field(description="The account, or the file's name.")
    state: Literal["connected", "closed"]
    credentials_hint: str | None = Field(description="The last 4 characters, to tell keys apart.")
    created_at: datetime


class OptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[Kind] | None = Field(
        default=None, description="What to bring. Default: contacts, tickets, articles, replies."
    )
    team_map: dict[str, UUID | None] = Field(
        default_factory=dict,
        description="A source group's id -> the CeDesk team its tickets go to (null: no team).",
    )
    sync: bool = Field(
        default=False,
        description="Keep syncing until go-live: after the import, the worker asks the source "
        "every 5 minutes for new tickets and new replies, and the credentials are kept until "
        "`sync-stop`. Not for a CSV file.",
    )


class GroupOut(BaseModel):
    external_id: str
    name: str
    suggested_team_id: UUID | None = Field(description="A team picked from the group's name.")
    suggested_team: str | None


class CountsOut(BaseModel):
    contacts: int
    tickets: int
    articles: int
    replies: int
    groups: int


class DiscoveryOut(BaseModel):
    counts: CountsOut
    group_name: str = Field(description='What the source calls groups: "Groups", "Mailboxes".')
    groups: list[GroupOut]
    status_map: dict[str, str] = Field(description="The source's status -> open/pending/resolved.")
    warnings: list[str]


class DryRunOut(BaseModel):
    will_create: dict[str, int] = Field(description="Per kind, what a run would add.")
    already_imported: dict[str, int] = Field(description="What this source brought before.")
    warnings: list[str] = Field(description='For example "No team picked for Billing."')


class RunOut(BaseModel):
    id: UUID
    connection_id: UUID
    source: str
    state: Literal["queued", "running", "done", "failed"]
    kinds: list[str]
    counts: dict[str, Any] = Field(
        description="Per kind: created and skipped (and, for tickets, unknown_agent_messages); "
        "failed: records left out because the source could not give them (listed by "
        "GET /imports/runs/{id}/failures); rows_left_out for a CSV."
    )
    sync_on: bool
    started_at: datetime
    finished_at: datetime | None
    last_error: str | None = Field(description="Why it paused or failed, in words.")
    last_sync_at: datetime | None = Field(
        default=None, description="When the source was last asked for changes (sync on)."
    )
    retry_attempts: int = Field(
        default=0,
        description="Failed tries of the page the run is on (a temporary error at the source). "
        "Back to 0 when a page is saved.",
    )
    outcome: Literal["in_progress", "complete", "complete_with_exceptions", "failed"] = Field(
        default="in_progress",
        description="complete: nothing was left out, cut, linked or noted. "
        "complete_with_exceptions: it ended, but see `report` (open failures, losses, a "
        "difference against the source's own counts). failed: it stopped; see last_error "
        "(a failed run can be resumed).",
    )
    report: "RunReportOut" = Field(default_factory=lambda: RunReportOut())


class LossOut(BaseModel):
    key: str = Field(description="What happened, e.g. files_linked, history_capped.")
    count: int
    meaning: str = Field(description="The same in words, for the Admin.")


class ReconciledOut(BaseModel):
    in_source: int | None = Field(
        description="What the source counted at the start (null: the source can't say)."
    )
    accounted_for: int = Field(description="Created + already here + noted as failed.")
    difference: int | None = Field(description="in_source - accounted_for when positive.")


class RunReportOut(BaseModel):
    open_failures: int = Field(
        default=0, description="Records noted and not resolved: GET /imports/runs/{id}/failures."
    )
    losses: list[LossOut] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list, description="What the source can't give (said before the run)."
    )
    reconciliation: dict[str, ReconciledOut] = Field(default_factory=dict)


class FailureOut(BaseModel):
    id: UUID
    kind: Literal["contact", "ticket", "article", "reply"]
    external_id: str = Field(description="The record's id in the source.")
    category: Literal["not_found", "temporary", "incomplete"] = Field(
        description="not_found: the source no longer has it (deleted after it was listed). "
        "temporary: it kept failing after every retry, so it was set aside. incomplete: it "
        "was imported, but part of its history could not be read (for example a Zendesk "
        "ticket whose comments answered 404: only its description came)."
    )
    attempts: int = Field(description="How many tries it got.")
    last_error: str = Field(description="What the source answered, in words.")
    first_failed_at: datetime
    last_failed_at: datetime
    status: Literal["open", "resolved", "dismissed"] = Field(
        description="resolved: read in full later (an import run again, or a sync look). "
        "dismissed: an Admin accepted what is missing."
    )
    resolved_at: datetime | None
    resolved_by_id: UUID | None = Field(
        default=None, description="The Admin who dismissed it (null otherwise, or if removed)."
    )


class FailuresOut(BaseModel):
    failures: list[FailureOut]
    next_cursor: str | None


class HistoryRunOut(BaseModel):
    id: UUID
    connection_id: UUID
    source: str = Field(
        description="intercom, zendesk, freshdesk, gorgias, helpscout, hubspot, zoho or csv."
    )
    account: str = Field(description="The account, or the file's name.")
    result: Literal["running", "finished", "syncing", "failed"]
    started_by_id: UUID | None
    started_by: str | None = Field(description="The Admin who started it (null if removed).")
    started_at: datetime
    finished_at: datetime | None
    kinds: list[str]
    counts: dict[str, Any] = Field(
        description="What it brought: per kind created and skipped; tickets also `files` "
        "copied and `files_linked`; `sync` has what the syncing looks added."
    )
    sync_on: bool
    last_sync_at: datetime | None
    last_error: str | None = Field(description="Why it failed or last paused, in words.")


class HistoryOut(BaseModel):
    runs: list[HistoryRunOut]
    next_cursor: str | None


def _connection_out(view: framework.ConnectionView) -> ConnectionOut:
    return ConnectionOut(
        id=view.id,
        source=view.source,
        label=view.label,
        state="closed" if view.state == "closed" else "connected",
        credentials_hint=view.credentials_hint,
        created_at=view.created_at,
    )


@router.post(
    "/imports/connections",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a help desk to import from (Admin)",
    description="The credentials are checked with the source, then kept encrypted. 422 when "
    "the source refuses them, 503 when it can't be reached. Intercom, Zendesk, Freshdesk, "
    "Gorgias and Help Scout (HubSpot and Zoho Desk come in Phase 3). Accepts "
    "`Idempotency-Key`.",
)
async def connect(
    body: ConnectIn,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ConnectionOut:
    credentials = {k: v.get_secret_value() for k, v in body.credentials.items()}
    sent = json.dumps([body.source, body.settings, credentials], sort_keys=True).encode()
    idem = idempotency(idempotency_key, "POST /imports/connections", sent)
    view = await framework.connect(
        sm,
        _importers(request),
        caller,
        source=body.source,
        settings=body.settings,
        credentials=credentials,
        idem=idem,
    )
    mark_replayed(idem, response)
    return _connection_out(view)


async def _read_file(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise TooLarge("The file is larger than 25 MB.")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise TooLarge("The file is larger than 25 MB.")
    return bytes(data)


@router.post(
    "/imports/csv",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV file to import (Admin)",
    description=(
        "The request body is the file itself (UTF-8 CSV), not a form. One row per message, "
        "grouped by `ticket_id`. Required columns: `ticket_id`, `subject`, `requester_email`, "
        "`message_from` (customer, agent or note), `message_body`, `message_created_at` "
        "(ISO 8601). Optional: `status`, `priority`, `channel`, `team`, `tags` (`;` between), "
        "`ticket_created_at`, `resolved_at`, `requester_name`, `assignee_email`, `csat_score`, "
        "`csat_comment`, `message_id`, `author_name`, `author_email`, and `field.<key>` for "
        "custom fields. At most 25 MB (413). The file is deleted when the run ends. Rows that "
        "can't be read are listed as warnings and left out; they never stop the import. "
        "Accepts `Idempotency-Key`."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses={413: {"description": "The file is larger than 25 MB."}},
)
async def upload_csv(
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    filename: Annotated[
        str, Query(min_length=1, max_length=200, description="The file's name, e.g. tickets.csv.")
    ],
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ConnectionOut:
    data = await _read_file(request)
    idem = idempotency(idempotency_key, "POST /imports/csv", filename.encode(), data)
    view = await framework.upload_csv(
        sm, _importers(request), caller, name=filename, data=data, idem=idem
    )
    mark_replayed(idem, response)
    return _connection_out(view)


@router.get(
    "/imports/connections/{connection_id}/discover",
    response_model=DiscoveryOut,
    summary="What the source holds, and the team each group probably maps to (Admin)",
)
async def discover(
    connection_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> DiscoveryOut:
    found = await framework.discover(sm, _importers(request), caller, connection_id)
    return DiscoveryOut(
        counts=CountsOut(
            contacts=found.counts.contacts,
            tickets=found.counts.tickets,
            articles=found.counts.articles,
            replies=found.counts.replies,
            groups=found.counts.groups,
        ),
        group_name=found.group_name,
        groups=[GroupOut(**g.__dict__) for g in found.groups],
        status_map=dict(found.status_map),
        warnings=found.warnings,
    )


def _options(body: OptionsIn) -> Mapping[str, Any]:
    return {
        "kinds": body.kinds,
        "team_map": {k: None if v is None else str(v) for k, v in body.team_map.items()},
    }


@router.post(
    "/imports/connections/{connection_id}/dry-run",
    response_model=DryRunOut,
    summary="What a run would do, saving nothing (Admin)",
)
async def dry_run(
    connection_id: UUID,
    body: OptionsIn,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
) -> DryRunOut:
    found = await framework.dry_run(sm, _importers(request), caller, connection_id, _options(body))
    return DryRunOut(
        will_create=found.will_create,
        already_imported=found.already_imported,
        warnings=found.warnings,
    )


@router.post(
    "/imports/connections/{connection_id}/run",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start importing (Admin)",
    description="The worker does the run in the background. 409 when this connection already "
    "has a run in progress, is syncing (stop it first) or was closed. 422 for `sync` on a "
    "CSV file. Poll `GET /imports/runs/{id}`. Accepts `Idempotency-Key`: a repeat returns "
    "the run the first one queued.",
)
async def start_run(
    connection_id: UUID,
    body: OptionsIn,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RunOut:
    sent = json.dumps([_options(body), body.sync], sort_keys=True).encode()
    idem = idempotency(idempotency_key, f"POST /imports/connections/{connection_id}/run", sent)
    view = await framework.start_run(
        sm, caller, connection_id, _options(body), idem=idem, sync=body.sync
    )
    mark_replayed(idem, response)
    return _run_out(view)


@router.post(
    "/imports/connections/{connection_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the stored credentials or file now (Admin)",
    description="The import history stays. 409 while a run is in progress.",
)
async def close_connection(
    connection_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> None:
    await framework.close_connection(sm, _importers(request), caller, connection_id)


@router.get("/imports/runs/{run_id}", response_model=RunOut, summary="A run's progress (Admin)")
async def get_run(run_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> RunOut:
    return _run_out(await framework.get_run(sm, caller, run_id))


@router.post(
    "/imports/runs/{run_id}/resume",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume a failed import where it stopped (Admin)",
    description="The same run is queued again and the worker carries on from its last saved "
    "page: the page that failed is read again, the ones before it are not, and nothing is "
    "made twice. Counts, options, team mapping, the sync setting and the run's failures are "
    "kept; `retry_attempts` starts from 0. 409 when the run isn't failed (in progress, "
    "finished or syncing), when its connection was closed or its credentials can't be read "
    "(connect again and start a new import), or when another run of the connection is in "
    "progress or syncing. 404 for an unknown run. Two resumes at once queue it once. Accepts "
    "`Idempotency-Key`: a repeat returns the run the first one queued.",
)
async def resume_run(
    run_id: UUID,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RunOut:
    idem = idempotency(idempotency_key, f"POST /imports/runs/{run_id}/resume", b"")
    view = await framework.resume_run(sm, _importers(request), caller, run_id, idem=idem)
    mark_replayed(idem, response)
    return _run_out(view)


@router.get(
    "/imports/runs/{run_id}/failures",
    response_model=FailuresOut,
    summary="Records a run could not read and left out (Admin)",
    description="Last failed first, cursor pages. A ticket deleted in the source after it was "
    "listed, or one that kept failing after every retry, is left out so the rest of the import "
    "goes on; one imported without all its history is listed as incomplete. They stay here "
    "after the run ends. Import again (or let a sync look run) to bring what comes back: the "
    "row shows resolved only once the record is read in full. 404 for an unknown run.",
)
async def run_failures(
    run_id: UUID, caller: CallerDep, sm: SessionmakerDep, cursor: Cursor = None, limit: Limit = 50
) -> FailuresOut:
    page = await framework.run_failures(sm, caller, run_id, cursor=cursor, limit=limit)
    return FailuresOut(
        failures=[FailureOut(**f.__dict__) for f in page.failures], next_cursor=page.next_cursor
    )


@router.post(
    "/imports/runs/{run_id}/failures/{failure_id}/dismiss",
    response_model=FailureOut,
    summary="Accept a record's loss: the failure ends as dismissed (Admin)",
    description="For an open failure only (409 otherwise; 404 for an unknown one). It records "
    "who decided. A later read of the record does not reopen or resolve it.",
)
async def dismiss_failure(
    run_id: UUID, failure_id: UUID, caller: CallerDep, sm: SessionmakerDep
) -> FailureOut:
    view = await framework.dismiss_failure(sm, caller, run_id, failure_id)
    return FailureOut(**view.__dict__)


@router.post(
    "/imports/runs/{run_id}/sync-now",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the source for changes now instead of in 5 minutes (Admin)",
    description="Only for a run that is syncing (`sync_on`, state `done`); the worker does the "
    "look within its next pass. 409 when the run isn't syncing or its first import is still "
    "running.",
)
async def sync_now(run_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> RunOut:
    return _run_out(await framework.sync_now(sm, caller, run_id))


@router.post(
    "/imports/runs/{run_id}/sync-stop",
    response_model=RunOut,
    summary="Go-live: stop syncing and delete the stored credentials (Admin)",
    description="CeDesk stops asking the source and keeps no access to it. The import history "
    "stays. 409 when the run isn't syncing.",
)
async def sync_stop(
    run_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> RunOut:
    return _run_out(await framework.sync_stop(sm, _importers(request), caller, run_id))


@router.get(
    "/imports/history",
    response_model=HistoryOut,
    summary="Every import: when, from where, by whom, how it went (Admin)",
    description="Newest first, cursor pages. `result` is running, finished, syncing (the "
    "import is done and still asking for changes) or failed (with `last_error`). To see the "
    "tickets an import brought, filter the ticket list with `imported`.",
)
async def history(
    caller: CallerDep, sm: SessionmakerDep, cursor: Cursor = None, limit: Limit = 50
) -> HistoryOut:
    page = await framework.history(sm, caller, cursor=cursor, limit=limit)
    return HistoryOut(
        runs=[HistoryRunOut(**r.__dict__) for r in page.runs], next_cursor=page.next_cursor
    )


def _run_out(view: framework.RunView) -> RunOut:
    return RunOut(
        id=view.id,
        connection_id=view.connection_id,
        source=view.source,
        state=view.state,
        kinds=view.kinds,
        counts=view.counts,
        sync_on=view.sync_on,
        started_at=view.started_at,
        finished_at=view.finished_at,
        last_error=view.last_error,
        last_sync_at=view.last_sync_at,
        retry_attempts=view.retry_attempts,
        outcome=view.outcome,
        report=RunReportOut.model_validate(view.report),
    )
