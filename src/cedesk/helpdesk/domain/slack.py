"""Slack alerts (md W20, PL7), the rules that need no database.

- An Admin installs the app with a Slack incoming-webhook URL (Slack > Apps > Incoming
  Webhooks); it is a secret (whoever has it can post to that channel), kept sealed.
- When a ticket is created Urgent or High, or a change makes it Urgent or High, one alert
  goes to that channel. The event comes from the outbox after the change commits, so it
  is what the rules made of the ticket, and it is retried if Slack doesn't answer.
- The alert has the ticket's number, subject, priority and team and a link to it; never
  the customer's messages.

Side conversations in Slack (md X5), the `slack` app: a Slack bot of the workspace's own.
- An Admin makes a Slack app (scopes chat:write, channels:history, groups:history,
  users:read; events message.channels and message.groups), installs it in Slack, and gives
  CeDesk the bot token (xoxb-...) and the signing secret. Both are sealed together.
- An agent starts a side conversation in a channel the bot is in: the bot posts the
  agent's message there, and the thread is the side conversation.
- Slack sends each message in the thread to the events URL. Its signature (HMAC-SHA256 of
  "v0:<timestamp>:<body>" with the signing secret, at most 5 minutes old) is checked
  before anything is read. A person's reply in the thread comes back to the ticket (with
  a photo or a file: its text and a "[file: name]" line per file; the file itself isn't
  fetched); a bot's message (ours included), an edit, a delete or a join is ignored.
- Slack's markup in a reply is made readable: <@U123> becomes @name (or @U123),
  <https://x|label> becomes "label (https://x)", and &lt; &gt; &amp; are unescaped.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

HOST = "hooks.slack.com"
ALERT_PRIORITIES = frozenset({"urgent", "high"})
EVENTS = frozenset({"ticket.created", "ticket.updated"})
SUBJECT_MAX = 150


class BadWebhook(ValueError):
    """The message is for the Admin."""


def check_webhook(raw: str) -> str:
    url = raw.strip()
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != HOST
        or parts.port not in (None, 443)
        or not parts.path.startswith("/services/")
        or parts.username
        or len(url) > 300
    ):
        raise BadWebhook("Paste the Incoming Webhook URL: https://hooks.slack.com/services/...")
    return url


def path_of(url: str) -> str:
    return urlsplit(url).path


def alerts_for(event: str, payload: dict[str, Any]) -> bool:
    """Whether this outbox event is an Urgent or High ticket to tell the channel about."""
    ticket = payload.get("ticket")
    if event not in EVENTS or not isinstance(ticket, dict):
        return False
    if ticket.get("priority") not in ALERT_PRIORITIES:
        return False
    if event == "ticket.created":
        return True
    changes = payload.get("changes")
    return isinstance(changes, list) and "priority" in changes


def _escape(text: str) -> str:
    """Slack's own escaping, so a subject can't make a link or mention anyone."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def message(ticket: dict[str, Any], label: str, url: str | None) -> dict[str, Any]:
    priority = str(ticket.get("priority") or "").capitalize()
    subject = _escape(str(ticket.get("subject") or "")[:SUBJECT_MAX])
    team = _escape(str(ticket.get("team") or "No team"))
    head = f"<{url}|{label}>" if url else label
    icon = ":red_circle:" if ticket.get("priority") == "urgent" else ":large_orange_circle:"
    return {"text": f"{icon} {priority}: {head} {subject} ({team})"}


# Side conversations in Slack (md X5)

API_HOST = "slack.com"
SIGNATURE_WINDOW = 300  # seconds; an older signed request is refused (a replay)
EVENT_MAX = 100_000  # bytes of one event
_TOKEN = re.compile(r"^xoxb-[A-Za-z0-9-]{10,240}$")
_SIGNING = re.compile(r"^[A-Za-z0-9]{20,100}$")
_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,29}$")
_CHANNEL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_TS = re.compile(r"^[0-9]{1,20}\.[0-9]{1,10}$")


class BadSlack(ValueError):
    """The message is for the person who typed it."""


def check_bot_token(raw: str) -> str:
    token = raw.strip()
    if not _TOKEN.match(token):
        raise BadSlack("Paste the bot token (xoxb-...) from the Slack app's OAuth page.")
    return token


def check_signing_secret(raw: str | None) -> str:
    secret = (raw or "").strip()
    if not _SIGNING.match(secret):
        raise BadSlack("Paste the signing secret from the Slack app's Basic Information page.")
    return secret


def seal_value(token: str, signing_secret: str) -> str:
    """What is sealed for the `slack` app: both secrets, never shown or logged."""
    return json.dumps({"token": token, "signing_secret": signing_secret})


def opened(value: str) -> tuple[str, str]:
    """(bot token, signing secret) from the sealed value."""
    try:
        data = json.loads(value)
        return str(data["token"]), str(data["signing_secret"])
    except (ValueError, KeyError, TypeError):
        raise BadSlack("The Slack app's secrets can't be read: install it again.") from None


def channel(raw: str) -> str:
    """What an agent types: #warehouse, warehouse, or a channel id (C0123ABCD)."""
    value = raw.strip()
    if _CHANNEL_ID.match(value):
        return value
    name = value.removeprefix("#").lower()
    if not _CHANNEL_NAME.match(name):
        raise BadSlack("Give a Slack channel: #warehouse, or its id (C0123ABCD).")
    return f"#{name}"


def signature_ok(
    signing_secret: str, timestamp: str | None, body: bytes, signature: str | None, now: float
) -> bool:
    """Slack's request signing: v0=HMAC-SHA256(secret, "v0:<timestamp>:<body>")."""
    if not timestamp or not signature or not (timestamp.isascii() and timestamp.isdigit()):
        return False  # isdigit() alone is true for "²", which int() refuses
    if abs(now - int(timestamp)) > SIGNATURE_WINDOW:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


@dataclass(frozen=True)
class ThreadReply:
    channel: str
    thread_ts: str
    ts: str
    user: str
    text: str  # with Slack's markup; `reply_body` makes it readable
    files: tuple[str, ...] = ()  # "[file: name]" lines


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


_MARKUP = re.compile(r"<([^<>]*)>")
_MENTION = re.compile(r"^@([UW][A-Z0-9]{2,39})(?:\|([^<>]*))?$")
_LINK = re.compile(r"^((?:https?|mailto):[^|<>]+)(?:\|([^<>]*))?$")
_CHANNEL_REF = re.compile(r"^#[CG][A-Z0-9]{6,29}\|([^<>]+)$")
_SPECIAL = re.compile(r"^!(here|channel|everyone)(?:\|[^<>]*)?$")
FILE_SHARE = "file_share"
FILES_MAX = 20  # "[file: ...]" lines of one reply
FILE_NAME_MAX = 200


def readable(text: str, names: Mapping[str, str] | None = None) -> str:
    """Slack's message markup as plain text: <@U123> -> @name (if known, else @U123),
    <https://x|label> -> "label (https://x)", <https://x> -> https://x, <#C1|name> ->
    #name, <!here> -> @here; then &lt; &gt; &amp; are unescaped (in that order, so
    "&amp;lt;" stays "&lt;")."""
    known = names or {}

    def one(found: re.Match[str]) -> str:
        inner = found.group(1)
        if mention := _MENTION.match(inner):
            user = mention.group(1)
            return "@" + (known.get(user) or user)
        if link := _LINK.match(inner):
            url, label = link.group(1), (link.group(2) or "").strip()
            return f"{label} ({url})" if label and label != url else url
        if ref := _CHANNEL_REF.match(inner):
            return "#" + ref.group(1)
        if special := _SPECIAL.match(inner):
            return "@" + special.group(1)
        return found.group(0)

    return _unescape(_MARKUP.sub(one, text))


def _file_lines(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    lines = []
    for item in files[:FILES_MAX]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("title")
        shown = str(name).strip()[:FILE_NAME_MAX] if isinstance(name, str) else ""
        lines.append(f"[file: {shown or 'unnamed'}]")
    return lines


def thread_reply(payload: Any) -> ThreadReply | None:
    """From an `event_callback`: a person's message in a thread, with a "[file: name]" line
    per file shared with it (the file isn't fetched); None for anything else (a bot's
    message, ours included; an edit, a delete or a join; a top-level message).

    `text` keeps Slack's markup (`readable` turns it into plain text, once the names of
    the people mentioned are known)."""
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict) or event.get("type") != "message":
        return None
    subtype = event.get("subtype")
    if subtype not in (None, "thread_broadcast", FILE_SHARE) or event.get("bot_id"):
        return None
    if subtype == FILE_SHARE and event.get("text") is None:
        event = {**event, "text": ""}  # a file with no words
    fields = [event.get(k) for k in ("channel", "thread_ts", "ts", "user", "text")]
    if not all(isinstance(v, str) for v in fields):
        return None
    channel_id, thread_ts, ts, user, text = (str(v) for v in fields)
    if thread_ts == ts or not _CHANNEL_ID.match(channel_id):
        return None
    if not _TS.match(thread_ts) or not _TS.match(ts):
        return None
    files = tuple(_file_lines(event.get("files")))
    return ThreadReply(channel_id, thread_ts, ts, user[:40], text, files)


def reply_body(reply: ThreadReply, names: Mapping[str, str] | None = None) -> str:
    """What is saved on the side conversation: the readable text, then the file lines."""
    return "\n".join([readable(reply.text, names).strip(), *reply.files]).strip()


def message_key(channel_id: str, ts: str) -> str:
    """`hd_side_message.header_message_id` of a Slack message: a retried event is saved once."""
    return f"slack:{channel_id}:{ts}"


def side_text(label: str, subject: str, body: str, author: str) -> str:
    """The bot's message: the ticket and the agent's words, escaped (no @channel, no links
    made from text). Never the customer's messages."""
    return (
        f"*{label}* {_escape(subject[:SUBJECT_MAX])}\n{_escape(body)}\n"
        f"_{_escape(author)}, from CeDesk. Reply in this thread; the customer doesn't see it._"
    )


def thread_text(body: str, author: str) -> str:
    """Writing again in the thread."""
    return f"{_escape(body)}\n_{_escape(author)}, from CeDesk_"


def api_problem(data: Any, where: str) -> tuple[str, bool] | None:
    """Slack's Web API answers 200 with `ok: false`: (message, permanent), or None if ok."""
    if isinstance(data, dict) and data.get("ok") is True:
        return None
    error = str(data.get("error") if isinstance(data, dict) else "") or "unknown"
    if error in ("invalid_auth", "not_authed", "account_inactive", "token_revoked",
                 "token_expired", "missing_scope"):  # fmt: skip
        return "Slack refused the bot token: install the Slack app again.", True
    if error == "not_in_channel":
        return f"The CeDesk bot isn't in {where}: type /invite @<your bot> there first.", True
    if error in ("channel_not_found", "is_archived"):
        return f"Slack has no open channel {where} the bot can see.", True
    if error == "ratelimited":
        return "Slack is busy; try again in a minute.", False
    return f"Slack refused it ({error[:60]}).", True
