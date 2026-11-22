"""Email campaigns (feature-gap plan phase 6): campaigns to a segment, their settings,
the suppression list, and the public unsubscribe and open-image routes.

Everything signed in needs `manage_campaigns` (Admins only; never an API key). Static
paths (`/campaigns/settings`) are declared before `/campaigns/{campaign_id}`.

Public (no sign-in), reached through the portal's host (the frontend proxy's `public`
area) or the API host:
- `GET/POST /public/unsubscribe/{token}`: the unsubscribe page and its button, and the
  mail app's one-click unsubscribe (RFC 8058 posts `List-Unsubscribe=One-Click` here).
  The same answer for any token; never the address.
- `GET /public/open/{token}`: the open-tracking image (a 1x1 GIF, always).
"""

import logging
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, model_validator

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, within
from cedesk.helpdesk.api.schemas import Body
from cedesk.helpdesk.domain import campaigns as rules
from cedesk.helpdesk.domain.campaigns import Limits, Links, Reason, State
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.service import campaigns
from cedesk.helpdesk.service.campaigns import CampaignView, Config
from cedesk.helpdesk.service.errors import TooManyAttempts
from cedesk.settings import Env, Settings

logger = logging.getLogger("cedesk.campaigns")
router = APIRouter(tags=["campaigns"])
public = APIRouter(tags=["public"])

C = "/campaigns"
TESTS_PER_HOUR = 20  # test emails per Admin


def config_of(settings: Settings) -> Config:
    """The caps and link addresses from the server's settings (the worker uses it too)."""
    page_url = settings.portal_url or settings.app_url
    return Config(
        limits=Limits(
            max_recipients=settings.campaign_max_recipients,
            daily_cap=settings.campaign_daily_cap,
            per_minute=settings.campaign_per_minute,
        ),
        links=Links(page_url=page_url, api_url=settings.api_url or page_url),
        require_https=settings.env is not Env.dev,
    )


def _config(request: Request) -> Config:
    return config_of(request.app.state.settings)


# Schemas

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=rules.NAME_MAX)
]
Subject = Annotated[str, StringConstraints(max_length=rules.SUBJECT_MAX)]
Text = Annotated[str, StringConstraints(max_length=rules.BODY_MAX)]
BODY_HELP = (
    "The email: text as in replies and macros, with the same placeholders "
    "({{customer.first_name}}, {{customer.name}}, {{agent.name}} = the sender's name, "
    "{{fields.<key>}} = the customer's contact field), and a little Markdown for the HTML "
    "part: paragraphs, line breaks, `#` headings, `-` and `1.` lists, **bold**, *italics*, "
    "[links](https://...). Raw HTML shows as text. The footer (sender, postal address, "
    "unsubscribe link) is added to every email."
)


class CampaignIn(Body):
    name: Name
    segment_id: UUID | None = None
    subject: Subject = ""
    body: Text = Field(default="", description=BODY_HELP)
    from_address_id: UUID | None = Field(
        default=None, description="One of the workspace's support addresses."
    )
    track_opens: bool = Field(
        default=False,
        description="Add an open-tracking image. Off by default: opens are approximate "
        "(Apple Mail and others load images for the reader), and in some places tracking "
        "needs the person's consent.",
    )


class CampaignPatch(Body):
    """Drafts only; send only what changes. `segment_id` and `from_address_id` take null."""

    name: Name | None = None
    segment_id: UUID | None = None
    subject: Subject | None = None
    body: Text | None = Field(default=None, description=BODY_HELP)
    from_address_id: UUID | None = None
    track_opens: bool | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set - {"segment_id", "from_address_id"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class StatsOut(BaseModel):
    recipients: int = Field(description="Copied from the segment when it started.")
    queued: int
    sending: int
    sent: int = Field(description="Accepted by the mail server (bounced ones included).")
    failed: int
    bounced: int = Field(description="Hard bounces reported back.")
    skipped: int = Field(description="Suppressed or erased after they were queued.")
    cancelled: int
    opened: int = Field(description="Recipients who opened at least once (if tracked).")
    opens: int = Field(description="Every open counted.")
    unsubscribed: int
    complained: int = Field(description="Marked it as spam (a feedback loop report).")


class CampaignOut(BaseModel):
    id: UUID
    name: str
    segment_id: UUID | None
    segment_name: str | None
    subject: str
    body: str
    from_address_id: UUID | None
    from_address: str | None
    from_name: str | None
    track_opens: bool
    state: State
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None = Field(description="Why it failed to start or was paused.")
    created_by: UUID | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime
    stats: StatsOut
    problems: list[str] = Field(
        description="A draft: what must be fixed before it can be sent (empty: ready)."
    )


class CampaignPageOut(BaseModel):
    campaigns: list[CampaignOut] = Field(description="Newest first. `problems` is empty here.")
    next_cursor: str | None


def _out(v: CampaignView) -> CampaignOut:
    return CampaignOut.model_validate(
        dict(v.campaign) | {"stats": StatsOut(**vars(v.stats)), "problems": v.problems}
    )


class AudienceOut(BaseModel):
    in_segment: int = Field(description="Customers in the segment now (erased ones never).")
    not_mailable: int = Field(
        description="Left out: a placeholder address (chat), a role address (postmaster@) "
        "or one of any workspace's support addresses."
    )
    suppressed: int = Field(description="Left out: on the suppression list.")
    recipients: int = Field(description="Who it would go to if sent now.")
    sent_last_24h: int = Field(description="Campaign emails this workspace sent in 24 hours.")
    queued: int = Field(
        description="Still to go in campaigns already sending or paused; counted against "
        "the daily cap with `sent_last_24h`."
    )
    daily_cap: int
    max_recipients: int
    problem: str | None = Field(description="Why it can't be sent with these numbers.")


class PreviewIn(Body):
    contact_id: UUID | None = Field(
        default=None,
        description="Fill it in for this customer; default: the first it would go to.",
    )


class PreviewOut(BaseModel):
    subject: str
    text: str = Field(description="The plain-text part, footer included.")
    html: str = Field(
        description="The HTML part, a whole page. Show it in a sandboxed iframe "
        '(`sandbox=""`, no scripts). Its links go nowhere.'
    )
    contact_id: UUID | None = Field(description="null: a made-up customer.")
    contact_name: str


class SentToOut(BaseModel):
    sent_to: str = Field(description="Your own address.")


class SendIn(Body):
    confirm: bool = Field(description='Must be true: the screen asks "Send to N people?" first.')
    scheduled_at: datetime | None = Field(
        default=None,
        description="Later (with its time zone, at most 90 days ahead); null or a past time: now.",
    )


class RecipientOut(BaseModel):
    id: UUID
    contact_id: UUID | None
    email: str | None = Field(description="null once the customer was erased.")
    name: str
    status: str = Field(description="queued, sending, sent, failed, bounced, skipped, cancelled")
    attempts: int
    error: str | None
    queued_at: datetime
    sent_at: datetime | None
    failed_at: datetime | None
    bounced_at: datetime | None
    first_opened_at: datetime | None
    open_count: int
    unsubscribed_at: datetime | None
    complained_at: datetime | None


class RecipientPageOut(BaseModel):
    recipients: list[RecipientOut] = Field(description="By address.")
    next_cursor: str | None


class SettingsIn(Body):
    postal_address: Annotated[str, StringConstraints(max_length=rules.POSTAL_MAX + 50)] = Field(
        description="Your business's postal address, shown in every campaign email's "
        "footer (anti-spam laws such as CAN-SPAM ask for one). 10-300 characters; lines kept."
    )


class SettingsOut(BaseModel):
    postal_address: str | None = Field(description="null: not set; no campaign can be sent.")
    updated_at: datetime | None
    max_recipients: int = Field(description="The most recipients one campaign has (server).")
    daily_cap: int = Field(description="Campaign emails per 24 hours per workspace (server).")
    per_minute: int = Field(description="Campaign emails a minute per workspace (server).")


class SuppressionOut(BaseModel):
    id: UUID
    email: str | None = Field(description="null: the customer was erased; only a hash is kept.")
    reason: Reason
    source: str = Field(description="link, one_click, bounce, complaint, admin, erase")
    campaign_id: UUID | None
    campaign_name: str | None
    note: str | None
    created_by: UUID | None
    created_by_name: str | None
    created_at: datetime


class SuppressionPageOut(BaseModel):
    entries: list[SuppressionOut] = Field(description="Newest first.")
    counts: dict[str, int] = Field(description="Entries per reason, the whole list.")
    next_cursor: str | None


class SuppressionIn(Body):
    email: Annotated[str, StringConstraints(min_length=3, max_length=320)]
    note: Annotated[str, StringConstraints(max_length=rules.NOTE_MAX)] | None = None


class RemoveIn(Body):
    confirm_consent: bool = Field(
        default=False,
        description="For an `unsubscribed` or `complaint` entry: the person asked to get "
        "campaigns again.",
    )
    justification: Annotated[str, StringConstraints(max_length=rules.NOTE_MAX)] | None = Field(
        default=None,
        description="For an `unsubscribed` or `complaint` entry: when and how they asked "
        "(10+ characters; kept in the audit log, addresses hidden).",
    )


_CAMPAIGN_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Not an Admin"},
    404: {"description": "Not found in this workspace"},
}


# Settings


def _settings_out(row: Any, config: Config) -> SettingsOut:
    return SettingsOut(
        postal_address=row["postal_address"] if row else None,
        updated_at=row["updated_at"] if row else None,
        max_recipients=config.limits.max_recipients,
        daily_cap=config.limits.daily_cap,
        per_minute=config.limits.per_minute,
    )


@router.get(C + "/settings", response_model=SettingsOut, summary="Campaign settings (Admin)")
async def get_settings(request: Request, caller: CallerDep, sm: SessionmakerDep) -> SettingsOut:
    return _settings_out(await campaigns.get_settings(sm, caller), _config(request))


@router.put(
    C + "/settings",
    response_model=SettingsOut,
    summary="Set the postal address campaign emails show (Admin; audited)",
    responses={422: {"description": "Too short or too long"}},
)
async def save_settings(
    body: SettingsIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> SettingsOut:
    row = await campaigns.save_settings(sm, caller, postal_address=body.postal_address)
    return _settings_out(row, _config(request))


# Campaigns


@router.get(C, response_model=CampaignPageOut, summary="The workspace's campaigns (Admin)")
async def list_campaigns(
    caller: CallerDep,
    sm: SessionmakerDep,
    state: State | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CampaignPageOut:
    found = await campaigns.list_campaigns(sm, caller, state=state, cursor=cursor, limit=limit)
    return CampaignPageOut(
        campaigns=[_out(v) for v in found.campaigns], next_cursor=found.next_cursor
    )


@router.post(
    C,
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a draft campaign (Admin; audited)",
    responses={**_CAMPAIGN_ERRORS, 422: {"description": "Bad field, or segment/address unknown"}},
)
async def create_campaign(
    body: CampaignIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> CampaignOut:
    view = await campaigns.create_campaign(sm, caller, body.model_dump(), _config(request))
    return _out(view)


@router.get(
    C + "/{campaign_id}",
    response_model=CampaignOut,
    summary="One campaign with its results (Admin)",
    responses=_CAMPAIGN_ERRORS,
)
async def get_campaign(
    campaign_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> CampaignOut:
    return _out(await campaigns.get_campaign(sm, caller, campaign_id, _config(request)))


@router.patch(
    C + "/{campaign_id}",
    response_model=CampaignOut,
    summary="Change a draft (Admin; audited: which fields, not the text)",
    responses={**_CAMPAIGN_ERRORS, 409: {"description": "Not a draft"}},
)
async def change_campaign(
    campaign_id: UUID,
    body: CampaignPatch,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
) -> CampaignOut:
    raw = {k: getattr(body, k) for k in body.model_fields_set}
    view = await campaigns.update_campaign(sm, caller, campaign_id, raw, _config(request))
    return _out(view)


@router.delete(
    C + "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft that never started (Admin; audited)",
    responses={**_CAMPAIGN_ERRORS, 409: {"description": "It sent (or is sending) emails"}},
)
async def delete_campaign(campaign_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> None:
    await campaigns.delete_campaign(sm, caller, campaign_id)


@router.get(
    C + "/{campaign_id}/audience",
    response_model=AudienceOut,
    summary="How many it would go to now, and why the rest won't (Admin)",
    responses={**_CAMPAIGN_ERRORS, 422: {"description": "No segment, or it was deleted"}},
)
async def campaign_audience(
    campaign_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> AudienceOut:
    found = await campaigns.audience(sm, caller, campaign_id, _config(request))
    return AudienceOut(**vars(found))


@router.post(
    C + "/{campaign_id}/preview",
    response_model=PreviewOut,
    summary="The email as one customer would get it (Admin)",
    responses=_CAMPAIGN_ERRORS,
)
async def preview_campaign(
    campaign_id: UUID,
    body: PreviewIn,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
) -> PreviewOut:
    found = await campaigns.preview(
        sm, caller, campaign_id, _config(request), contact_id=body.contact_id
    )
    r = found.rendered
    return PreviewOut(subject=r.subject, text=r.text, html=r.html,
                      contact_id=found.contact_id, contact_name=found.contact_name)  # fmt: skip


@router.post(
    C + "/{campaign_id}/test",
    response_model=SentToOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email it to yourself only (Admin; audited)",
    description=f"Subject starts with `[Test]`; its unsubscribe link goes nowhere. At most "
    f"{TESTS_PER_HOUR} an hour per Admin. Goes out with the next worker pass.",
    responses={
        **_CAMPAIGN_ERRORS,
        422: {"description": "No address, subject or text yet"},
        429: {"description": "Too many test emails"},
    },
)
async def test_campaign(
    campaign_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> SentToOut:
    within(request.app.state.campaign_tests, str(caller.member_id), "test emails")
    return SentToOut(sent_to=await campaigns.send_test(sm, caller, campaign_id, _config(request)))


@router.post(
    C + "/{campaign_id}/send",
    response_model=CampaignOut,
    summary="Send now or schedule (Admin; confirm required; audited)",
    description="Checks first (422 with every problem in the message): subject, text, "
    "segment, a verified support address with SPF and DKIM passing, the postal address, "
    "the caps, and `confirm: true`. Now: the recipients are copied from the segment and it "
    "is `sending`. Later: `scheduled`; checked again when its time comes (it is `failed` "
    "then if a check no longer passes).",
    responses={
        **_CAMPAIGN_ERRORS,
        409: {"description": "Not a draft"},
        422: {"description": "Not confirmed, not ready, or over a cap"},
    },
)
async def send_campaign(
    campaign_id: UUID, body: SendIn, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> CampaignOut:
    view = await campaigns.send(
        sm, caller, campaign_id, _config(request), confirm=body.confirm,
        scheduled_at=body.scheduled_at,
    )  # fmt: skip
    return _out(view)


@router.post(
    C + "/{campaign_id}/cancel",
    response_model=CampaignOut,
    summary="Cancel: a scheduled one goes back to draft; a sending one stops (Admin; audited)",
    responses={**_CAMPAIGN_ERRORS, 409: {"description": "Not scheduled, sending or paused"}},
)
async def cancel_campaign(campaign_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> CampaignOut:
    return _out(await campaigns.cancel(sm, caller, campaign_id))


@router.post(
    C + "/{campaign_id}/pause",
    response_model=CampaignOut,
    summary="Pause a sending campaign (Admin; audited)",
    responses={**_CAMPAIGN_ERRORS, 409: {"description": "Not sending"}},
)
async def pause_campaign(campaign_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> CampaignOut:
    return _out(await campaigns.pause(sm, caller, campaign_id))


@router.post(
    C + "/{campaign_id}/resume",
    response_model=CampaignOut,
    summary="Resume a paused campaign (Admin; audited)",
    description="A campaign is also paused by itself when over 5% of 50+ sent emails bounced.",
    responses={**_CAMPAIGN_ERRORS, 409: {"description": "Not paused"}},
)
async def resume_campaign(campaign_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> CampaignOut:
    return _out(await campaigns.resume(sm, caller, campaign_id))


@router.get(
    C + "/{campaign_id}/stats",
    response_model=StatsOut,
    summary="A campaign's totals (Admin)",
    responses=_CAMPAIGN_ERRORS,
)
async def campaign_stats(
    campaign_id: UUID, request: Request, caller: CallerDep, sm: SessionmakerDep
) -> StatsOut:
    view = await campaigns.get_campaign(sm, caller, campaign_id, _config(request))
    return StatsOut(**vars(view.stats))


StatusQ = Annotated[
    Literal[
        "queued", "sending", "sent", "failed", "bounced", "skipped", "cancelled",
        "opened", "unsubscribed", "complained",
    ] | None,
    Query(description="A status, or opened / unsubscribed / complained."),
]  # fmt: skip


@router.get(
    C + "/{campaign_id}/recipients",
    response_model=RecipientPageOut,
    summary="A campaign's recipients and their results, a page at a time (Admin)",
    responses=_CAMPAIGN_ERRORS,
)
async def campaign_recipients(
    campaign_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    status: StatusQ = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> RecipientPageOut:
    found = await campaigns.recipients(
        sm, caller, campaign_id, status=status, cursor=cursor, limit=limit
    )
    return RecipientPageOut(
        recipients=[RecipientOut.model_validate(dict(r)) for r in found.recipients],
        next_cursor=found.next_cursor,
    )


@router.get(
    C + "/{campaign_id}/recipients/export",
    summary="Download every recipient's result as CSV (Admin; audited)",
    description="email, name, status, queued_at, sent_at, bounced_at, first_opened_at, "
    "open_count, unsubscribed_at, complained_at, error. UTF-8 with a byte order mark; a "
    "value a spreadsheet would run as a formula starts with `'`.",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}, **_CAMPAIGN_ERRORS},
)
async def export_recipients(campaign_id: UUID, caller: CallerDep, sm: SessionmakerDep) -> Response:
    file = await campaigns.export_results(sm, caller, campaign_id)
    return Response(
        content=file.body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file.name}"'},
    )


# The suppression list

S = "/suppressions"


@router.get(S, response_model=SuppressionPageOut, summary="The suppression list (Admin)")
async def list_suppressions(
    caller: CallerDep,
    sm: SessionmakerDep,
    reason: Reason | None = None,
    email: Annotated[
        str | None, Query(max_length=320, description="Is this one address on it? (exact)")
    ] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SuppressionPageOut:
    found = await campaigns.list_suppressions(
        sm, caller, reason=reason, email=email, cursor=cursor, limit=limit
    )
    return SuppressionPageOut(
        entries=[SuppressionOut.model_validate(dict(r)) for r in found.entries],
        counts=dict(found.counts),
        next_cursor=found.next_cursor,
    )


@router.post(
    S,
    response_model=SuppressionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an address: campaigns never go to it (Admin; audited)",
    responses={409: {"description": "Already on the list"}, 422: {"description": "Bad address"}},
)
async def add_suppression(
    body: SuppressionIn, caller: CallerDep, sm: SessionmakerDep
) -> SuppressionOut:
    row = await campaigns.add_suppression(sm, caller, email=body.email, note=body.note)
    return SuppressionOut.model_validate(dict(row))


@router.post(
    S + "/{suppression_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take an address off the list (Admin; audited)",
    description="`manual` and `hard_bounce`: removed. `unsubscribed` and `complaint`: only "
    "with `confirm_consent: true` and a `justification` (the person asked to get campaigns "
    "again). An erased customer's entry: never (409).",
    responses={404: {"description": "Not found"}, 409: {"description": "Refused; see message"}},
)
async def remove_suppression(
    suppression_id: UUID, body: RemoveIn, caller: CallerDep, sm: SessionmakerDep
) -> None:
    await campaigns.remove_suppression(
        sm, caller, suppression_id, confirm_consent=body.confirm_consent,
        justification=body.justification,
    )  # fmt: skip


# Public: unsubscribe and the open image

_TOKEN = Annotated[str, StringConstraints(min_length=10, max_length=200)]


class UnsubscribePageOut(BaseModel):
    workspace_name: str | None = Field(
        description="Who the emails are from; null: the link is not known (show a general "
        "page that still offers the button)."
    )
    unsubscribed: bool = Field(description="Already done.")


class UnsubscribedOut(BaseModel):
    unsubscribed: Literal[True] = True
    message: str = "You won't get these emails any more."


def _limit_unsubscribe(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.unsubscribe_attempts.hit(ip)
    if wait is not None:
        raise TooManyAttempts(f"Too many requests. Try again in {wait} seconds.", wait)


@public.get(
    "/public/unsubscribe/{token}",
    response_model=UnsubscribePageOut,
    summary="The unsubscribe page behind a campaign email's link (no sign-in)",
    description="The page reads the token from its link's `#token=`. Changes nothing (mail "
    "scanners open links): the page's button posts.",
    responses={429: {"description": "Too many requests from this IP"}},
)
async def unsubscribe_page(
    token: _TOKEN, request: Request, sm: SessionmakerDep
) -> UnsubscribePageOut:
    _limit_unsubscribe(request)
    found = await campaigns.unsubscribe_info(sm, token)
    return UnsubscribePageOut(workspace_name=found.workspace_name, unsubscribed=found.unsubscribed)


@public.post(
    "/public/unsubscribe/{token}",
    response_model=UnsubscribedOut,
    summary="Unsubscribe from a workspace's campaigns (no sign-in; RFC 8058 one-click)",
    description="The page's button, and the mail app's one-click unsubscribe (it posts "
    "`List-Unsubscribe=One-Click` as a form; any body is accepted). Always 200 with the "
    "same answer; doing it again changes nothing. Ticket replies still reach the person.",
    responses={429: {"description": "Too many requests from this IP"}},
)
async def unsubscribe(token: _TOKEN, request: Request, sm: SessionmakerDep) -> UnsubscribedOut:
    _limit_unsubscribe(request)
    content_type = request.headers.get("content-type", "")
    one_click = content_type.startswith("application/x-www-form-urlencoded") or (
        content_type.startswith("multipart/form-data")
    )
    await campaigns.unsubscribe(sm, token, one_click=one_click)
    return UnsubscribedOut()


@public.get(
    "/public/open/{token}",
    response_class=Response,
    summary="The open-tracking image in a campaign email (no sign-in)",
    description="Always a 1x1 transparent GIF, never an error. Counts the first open and "
    "every open (approximate: some mail apps load images for the reader).",
    responses={200: {"content": {"image/gif": {}}}},
)
async def open_image(token: str, request: Request, sm: SessionmakerDep) -> Response:
    ip = request.client.host if request.client else "unknown"
    if 10 <= len(token) <= 200 and request.app.state.unsubscribe_attempts.hit(ip) is None:
        try:
            await campaigns.record_open(sm, token)
        except Exception:  # an image never fails: the open is just not counted
            logger.exception("campaign open not recorded")
    return Response(
        content=rules.PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, private, max-age=0"},
    )
