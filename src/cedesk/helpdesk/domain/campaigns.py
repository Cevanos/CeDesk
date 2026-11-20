"""Email campaigns (feature-gap plan phase 6), the rules that need no database.

- States: a draft is edited; `send` makes it `scheduled` (a time ahead) or starts it now
  (`sending`, recipients copied from the segment). The worker sends it in batches; when
  every recipient is done it is `sent`. Too many hard bounces pause it (`paused`); an
  Admin resumes or cancels it. A scheduled campaign cancelled goes back to `draft`
  (nothing was sent); once sending started, cancel makes it `cancelled`. `failed`: it
  could not start (its segment or address was deleted, a cap, no postal address).
- The suppression list is keyed by `email_hash`: sha256("<workspace id>:<address>"), the
  same expression `repo/campaigns.py` uses in SQL. An erased customer keeps only the hash.
- Content: the body is the text replies and macros use, with the same placeholders
  (`domain/placeholders.py`) plus a little Markdown for the HTML part (paragraphs, line
  breaks, `#` headings, `-` and `1.` lists, `**bold**`, `*italics*`, `[links](https://…)`).
  Raw HTML is never passed through: it shows as text. A customer's name or field value
  put in by a placeholder is escaped, so it can't add a link or markup.
- Every email ends with a footer: who sent it, the workspace's postal address and the
  unsubscribe link; the headers carry RFC 8058 one-click unsubscribe. Open tracking (a
  1x1 image) is per campaign and off unless turned on.
"""

import hashlib
import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from cedesk.helpdesk.domain.placeholders import render as fill

NAME_MAX = 120
SUBJECT_MAX = 200
BODY_MAX = 50_000
POSTAL_MIN, POSTAL_MAX = 10, 300
NOTE_MAX = 200
JUSTIFY_MIN = 10
TEST_PREFIX = "[Test] "
MAX_CAMPAIGNS = 1000  # per workspace (drafts and sent ones together)
SEND_ATTEMPTS = 3  # tries of one recipient on a temporary mail server error
LEASE = timedelta(minutes=5)  # a claimed recipient not marked by then was interrupted
SCHEDULE_AHEAD = timedelta(days=90)
# Pause a campaign when this share of its sent emails bounced hard, once enough were sent.
BOUNCE_PAUSE_MIN_SENT = 50
BOUNCE_PAUSE_RATE = 0.05
# Addresses never mailed: role mailboxes of mail systems.
ROLE_LOCALS = frozenset({"postmaster", "mailer-daemon", "abuse", "noreply", "no-reply",
                         "donotreply", "do-not-reply", "hostmaster", "webmaster"})  # fmt: skip
INTERRUPTED = "The worker stopped while sending; not sent again so no one gets it twice."
# The open-tracking image: a transparent 1x1 GIF.
PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


class State(StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    paused = "paused"
    sent = "sent"
    cancelled = "cancelled"
    failed = "failed"


class RecipientStatus(StrEnum):
    queued = "queued"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    bounced = "bounced"
    skipped = "skipped"  # suppressed or erased after it was queued
    cancelled = "cancelled"


# The recipient list's filters: a status, or a result that is not a status.
RECIPIENT_FILTERS = (*[s.value for s in RecipientStatus], "opened", "unsubscribed", "complained")


class Reason(StrEnum):
    unsubscribed = "unsubscribed"
    hard_bounce = "hard_bounce"
    complaint = "complaint"
    manual = "manual"
    erased = "erased"


class Source(StrEnum):
    link = "link"  # the unsubscribe page
    one_click = "one_click"  # the mail app's button (RFC 8058)
    bounce = "bounce"
    complaint = "complaint"
    admin = "admin"
    erase = "erase"


# A person chose these (or their data is gone): an Admin can't simply take them off.
PROTECTED = frozenset({Reason.unsubscribed, Reason.complaint, Reason.erased})

EDITABLE = frozenset({State.draft})
CANCELLABLE = frozenset({State.scheduled, State.sending, State.paused})
LIVE = frozenset({State.sending, State.paused})


class CampaignError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    """Server-wide caps (CEDESK_CAMPAIGN_*), the same for every workspace."""

    max_recipients: int = 5_000  # per campaign
    daily_cap: int = 10_000  # emails per workspace per 24 hours, all campaigns
    per_minute: int = 60  # emails per workspace per minute
    batch: int = 50  # claimed per worker pass per campaign


@dataclass(frozen=True)
class Links:
    """Where the links in a campaign email point.

    `page_url`: the customer-facing site (the portal), which shows the unsubscribe page at
    `/unsubscribe#token=…` (the token after `#` never reaches a server log). `api_url`: the
    API's public address, for the one-click POST (RFC 8058 needs a URL that takes a POST
    itself) and the open image."""

    page_url: str
    api_url: str

    def unsubscribe_page(self, token: str) -> str:
        return f"{self.page_url.rstrip('/')}/unsubscribe#token={token}"

    def one_click(self, token: str) -> str:
        return f"{self.api_url.rstrip('/')}/api/v1/public/unsubscribe/{token}"

    def pixel(self, token: str) -> str:
        return f"{self.api_url.rstrip('/')}/api/v1/public/open/{token}"

    def problem(self, *, require_https: bool) -> str | None:
        for url in (self.page_url, self.api_url):
            if not url.startswith(("https://", "http://")):
                return "The server has no public address for unsubscribe links (CEDESK_APP_URL)."
            if require_https and not url.startswith("https://"):
                return (
                    "Unsubscribe links need an https address (CEDESK_PORTAL_URL, CEDESK_API_URL)."
                )
        return None


def email_hash(workspace_id: UUID, email: str) -> bytes:
    """The suppression key. Keyed by workspace, so the same address hashes differently in
    two workspaces."""
    return hashlib.sha256(f"{workspace_id}:{email.strip().lower()}".encode()).digest()


def mailable(email: str) -> bool:
    """Not a placeholder (chat customers' `.invalid`, an erased customer's) and not a mail
    system's role mailbox."""
    value = email.strip().lower()
    local, _, domain = value.partition("@")
    return bool(local and domain) and not domain.endswith(".invalid") and local not in ROLE_LOCALS


def clean_name(raw: str) -> str:
    value = " ".join(raw.split())
    if not 1 <= len(value) <= NAME_MAX:
        raise CampaignError(f"A campaign name is 1-{NAME_MAX} characters.")
    return value


def clean_subject(raw: str) -> str:
    value = " ".join(raw.split())
    if len(value) > SUBJECT_MAX:
        raise CampaignError(f"A subject is at most {SUBJECT_MAX} characters.")
    return value


def clean_body(raw: str) -> str:
    value = raw.replace("\r\n", "\n").strip()
    if len(value) > BODY_MAX:
        raise CampaignError(f"The email is at most {BODY_MAX} characters.")
    return value


def clean_postal(raw: str) -> str:
    value = "\n".join(line.strip() for line in raw.strip().splitlines() if line.strip())
    if not POSTAL_MIN <= len(value) <= POSTAL_MAX:
        raise CampaignError(
            f"The postal address is {POSTAL_MIN}-{POSTAL_MAX} characters, e.g. "
            "'Acme Pvt Ltd, 12 MG Road, Bengaluru 560001, India'."
        )
    return value


def clean_note(raw: str | None) -> str | None:
    value = " ".join((raw or "").split())
    if len(value) > NOTE_MAX:
        raise CampaignError(f"A note is at most {NOTE_MAX} characters.")
    return value or None


_EMAIL_LIKE = re.compile(r"[^\s@]+@[^\s@]+")


def without_addresses(text: str) -> str:
    """For the audit log: an Admin's typed reason with any address in it hidden."""
    return _EMAIL_LIKE.sub("(address)", text)[:NOTE_MAX]


def check_schedule(at: datetime | None, now: datetime) -> datetime | None:
    """None or a time not ahead: send now. A time too far ahead is refused."""
    if at is None or at <= now:
        return None
    if at.tzinfo is None:
        raise CampaignError("Give the time with its time zone, e.g. 2026-12-01T10:00:00+05:30.")
    if at - now > SCHEDULE_AHEAD:
        raise CampaignError(f"Schedule at most {SCHEDULE_AHEAD.days} days ahead.")
    return at


def ready_problems(
    *,
    subject: str,
    body: str,
    segment: bool,
    sender: bool,
    sender_verified: bool,
    dns_ok: bool,
    postal_address: bool,
) -> list[str]:
    """Why a campaign can't be sent yet (empty: it can)."""
    problems = []
    if not subject:
        problems.append("Add a subject.")
    if not body:
        problems.append("Write the email.")
    if not segment:
        problems.append("Pick a segment to send to.")
    if not sender:
        problems.append("Pick the support address it comes from.")
    elif not sender_verified:
        problems.append(
            "Verify the support address first (Settings > Email > check forwarding), so "
            "bounces come back to CeDesk."
        )
    elif not dns_ok:
        problems.append(
            "The support address's domain needs SPF and DKIM passing (Settings > Email > "
            "check DNS), or campaign mail will land in spam and hurt your support email too."
        )
    if not postal_address:
        problems.append("Add your postal address (Campaigns > Settings); every email shows it.")
    return problems


def dns_ok(result: Mapping[str, Any] | None) -> bool:
    """The address's last DNS check found SPF and DKIM right."""
    if not isinstance(result, Mapping):
        return False
    return all(
        isinstance(result.get(k), Mapping) and result[k].get("status") == "ok"
        for k in ("spf", "dkim")
    )


def cap_problem(recipients: int, sent_today: int, limits: Limits) -> str | None:
    """`sent_today`: sent in the last 24 hours plus still queued in other campaigns."""
    if recipients == 0:
        return "No one to send to: the segment is empty once suppressed addresses are left out."
    if recipients > limits.max_recipients:
        return (
            f"This campaign would go to {recipients} people; the most is "
            f"{limits.max_recipients}. Narrow the segment."
        )
    if sent_today + recipients > limits.daily_cap:
        left = max(0, limits.daily_cap - sent_today)
        return (
            f"Over the daily limit: {left} more campaign emails can go out in the next 24 "
            f"hours (the limit is {limits.daily_cap})."
        )
    return None


def should_pause(sent: int, bounced: int) -> bool:
    return sent >= BOUNCE_PAUSE_MIN_SENT and bounced > sent * BOUNCE_PAUSE_RATE


def retry_wait(attempts: int) -> timedelta | None:
    """After `attempts` tries with a temporary error: how long to wait, or None to give up."""
    if attempts >= SEND_ATTEMPTS:
        return None
    return timedelta(minutes=5 * attempts)


# Content


_MD_SPECIAL = re.compile(r"([\\`*_\[\]()#+\-.!<>|~])")


def md_escape(value: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", value)


def fill_in(
    text: str,
    *,
    customer_name: str,
    sender_name: str,
    fields: Mapping[str, object],
    for_html: bool,
) -> str:
    """The placeholders filled. For the HTML part each value is escaped first, so a
    customer named `[win](https://x)` stays a name."""
    if not for_html:
        return fill(text, customer_name=customer_name, agent_name=sender_name, fields=fields)
    safe = {k: md_escape(str(v)) if v is not None else None for k, v in fields.items()}
    return fill(
        text,
        customer_name=md_escape(customer_name),
        agent_name=md_escape(sender_name),
        fields=safe,
    )


_URL = re.compile(r"^(https?://|mailto:)[^\s<>\"']+$", re.IGNORECASE)
_INLINE = re.compile(
    r"\\(?P<esc>[\\`*_\[\]()#+\-.!<>|~])"
    r"|\[(?P<text>[^\]\n]{1,500})\]\((?P<url>[^)\s]{1,2000})\)"
    r"|\*\*(?P<bold>[^*\n]+?)\*\*"
    r"|\*(?P<em>[^*\n]+?)\*"
    r"|(?P<bare>https?://[^\s<>\"'\]\)]{1,2000})"
)


def _inline(text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[last : m.start()]))
        last = m.end()
        if m.group("esc"):
            out.append(html.escape(m.group("esc")))
        elif m.group("url") is not None:
            url = m.group("url")
            label = _inline(m.group("text"))
            if _URL.match(url):
                out.append(f'<a href="{html.escape(url, quote=True)}">{label}</a>')
            else:
                out.append(label)
        elif m.group("bold") is not None:
            out.append(f"<strong>{_inline(m.group('bold'))}</strong>")
        elif m.group("em") is not None:
            out.append(f"<em>{_inline(m.group('em'))}</em>")
        else:
            url = m.group("bare")
            out.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
    out.append(html.escape(text[last:]))
    return "".join(out)


_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
_BULLET = re.compile(r"^[-*]\s+(.+)$")
_NUMBER = re.compile(r"^\d{1,3}[.)]\s+(.+)$")


def _items(pattern: re.Pattern[str], lines: list[str]) -> str:
    """The lines as list items when every one is an item of this kind, else ""."""
    found = [pattern.match(line) for line in lines]
    if not all(found):
        return ""
    return "".join(f"<li>{_inline(m.group(1))}</li>" for m in found if m)


def to_html(text: str) -> str:
    """The body (placeholders already filled) as safe HTML."""
    blocks = re.split(r"\n\s*\n", text.strip())
    out: list[str] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        heading = _HEADING.match(lines[0]) if len(lines) == 1 else None
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullets := _items(_BULLET, lines):
            out.append(f"<ul>{bullets}</ul>")
        elif numbered := _items(_NUMBER, lines):
            out.append(f"<ol>{numbered}</ol>")
        else:
            out.append("<p>" + "<br>\n".join(_inline(line) for line in lines) + "</p>")
    return "\n".join(out)


_MD_LINK = re.compile(r"\[([^\]\n]{1,500})\]\(([^)\s]{1,2000})\)")


def to_text(text: str) -> str:
    """The plain-text part: as written, a link shown as 'text (url)'."""
    return _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text.strip())


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class Footer:
    workspace_name: str
    postal_address: str
    unsubscribe_url: str
    pixel_url: str | None = None


def footer_text(f: Footer) -> str:
    return (
        "\n\n-- \n"
        f"You get this email from {f.workspace_name}.\n"
        f"{f.postal_address}\n"
        f"Unsubscribe: {f.unsubscribe_url}\n"
    )


def footer_html(f: Footer) -> str:
    postal = "<br>".join(html.escape(line) for line in f.postal_address.splitlines())
    pixel = (
        f'<img src="{html.escape(f.pixel_url, quote=True)}" width="1" height="1" alt="" '
        'style="display:block;border:0;width:1px;height:1px">'
        if f.pixel_url
        else ""
    )
    return (
        '<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">'
        '<p style="color:#666;font-size:12px;line-height:1.5">'
        f"You get this email from {html.escape(f.workspace_name)}.<br>{postal}<br>"
        f'<a href="{html.escape(f.unsubscribe_url, quote=True)}">Unsubscribe</a></p>{pixel}'
    )


def page(body_html: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;'
        'color:#222;max-width:640px;margin:0 auto;padding:16px">'
        f"{body_html}</body></html>"
    )


def render(
    *,
    subject: str,
    body: str,
    customer_name: str,
    sender_name: str,
    fields: Mapping[str, object],
    footer: Footer,
) -> Rendered:
    """One recipient's email: subject, plain text and HTML, the footer added to both."""
    filled_subject = fill_in(
        subject, customer_name=customer_name, sender_name=sender_name, fields=fields,
        for_html=False,
    )  # fmt: skip
    # Links are written out before the placeholders are filled, so only the Admin's own
    # Markdown links change; a customer's value stays exactly as it is.
    plain = fill_in(
        to_text(body), customer_name=customer_name, sender_name=sender_name, fields=fields,
        for_html=False,
    )  # fmt: skip
    marked = fill_in(
        body, customer_name=customer_name, sender_name=sender_name, fields=fields, for_html=True
    )
    return Rendered(
        subject=" ".join(filled_subject.split())[: SUBJECT_MAX * 2],
        text=plain.strip() + footer_text(footer),
        html=page(to_html(marked) + footer_html(footer)),
    )


def unsubscribe_headers(one_click_url: str) -> tuple[tuple[str, str], ...]:
    """RFC 2369 and RFC 8058: the mail app's own unsubscribe button posts to the URL."""
    return (
        ("List-Unsubscribe", f"<{one_click_url}>"),
        ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
        ("Precedence", "bulk"),
        ("X-Auto-Response-Suppress", "OOF, AutoReply"),
    )
