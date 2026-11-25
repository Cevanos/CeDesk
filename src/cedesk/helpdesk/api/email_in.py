"""Email in by webhook (md C8): a mail service posts each email sent to a support address
here, as the raw email. Not for the frontend.

- Raw email: `Content-Type: message/rfc822` (or any non-form type), the email as the body.
- A form (multipart or url-encoded) with the raw email in `body-mime` (Mailgun "raw
  MIME" forwarding) or `email` (SendGrid Inbound Parse, "send raw"); the delivery
  address in `recipient` or `to`.

The token from `CEDESK_INBOUND_EMAIL_TOKEN` comes as `Authorization: Bearer <token>`, or as
the password of HTTP Basic auth (for services that can only put it in the URL). Without
that setting the route answers 404. Every handled email answers 200, also when it is
ignored on purpose, so the service does not send it again; only errors answer 5xx.
"""

import base64
import binascii
import hmac
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Literal
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from cedesk.helpdesk.service import email_in
from cedesk.helpdesk.service.errors import Invalid, NotFound, NotSignedIn, TooLarge
from cedesk.settings import Settings

router = APIRouter(tags=["email in"])

FORM_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")


class ReceivedOut(BaseModel):
    outcome: Literal[
        "created", "added", "rules_ran", "reopened", "thanked", "duplicate",
        "automatic", "from_us", "no_workspace", "unreadable", "verification", "bounce",
        "side_reply",
    ] = Field(description="created: a new ticket; added / rules_ran / reopened / thanked: on "
              "an existing ticket; side_reply: an answer in a side conversation (saved there, "
              "never on the customer's thread; ticket_id is its ticket); verification: a "
              "support address's forwarding check came back (no ticket); bounce: a campaign "
              "email's bounce or spam report (recorded, no ticket); the rest: ignored on "
              "purpose.")  # fmt: skip
    ticket_id: UUID | None


def _given_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
        return decoded.partition(":")[2]
    return None


def _check(request: Request, settings: Settings) -> None:
    expected = settings.inbound_email_token
    if expected is None:
        raise NotFound("Not found.")
    given = _given_token(request)
    if given is None or not hmac.compare_digest(
        given.encode(), expected.get_secret_value().encode()
    ):
        raise NotSignedIn("Send the inbound email token.")


async def _raw(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise TooLarge(f"An email can be at most {limit} bytes.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise TooLarge(f"An email can be at most {limit} bytes.")
    return bytes(body)


def _form(body: bytes, content_type: str) -> dict[str, str]:
    """The text fields of a form, read with the standard library (no extra package)."""
    if content_type.lower().startswith("application/x-www-form-urlencoded"):
        pairs = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v for k, v in pairs[:50]}
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    if not isinstance(message, EmailMessage) or not message.is_multipart():
        raise Invalid("The form can't be read.")
    for part in list(message.iter_parts())[:50]:
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or part.get_filename():
            continue  # attachments sent as files are not read here
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, "replace")
    return fields


@router.post(
    "/inbound/email",
    response_model=ReceivedOut,
    summary="A mail service delivers one incoming email (token required)",
)
async def receive_email(request: Request) -> ReceivedOut:
    settings: Settings = request.app.state.settings
    _check(request, settings)
    limit = settings.inbound_email_max_bytes
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = await _raw(request, limit)
    recipients: list[str] = []
    if content_type in FORM_TYPES:
        fields = _form(raw, request.headers.get("content-type", ""))
        mime = fields.get("body-mime") or fields.get("email")
        if not mime:
            raise Invalid("Send the raw email as `body-mime` or `email`.")
        raw = mime.encode()
        delivered = fields.get("recipient") or fields.get("to") or ""
        recipients = [r.strip() for r in delivered.split(",") if r.strip()][:20]
    sm = request.app.state.sessionmaker
    result = await email_in.receive(
        sm,
        raw,
        recipients=recipients,
        store=request.app.state.store,
        file_max_bytes=settings.attachment_max_bytes,
    )
    return ReceivedOut(outcome=result.outcome.value, ticket_id=result.ticket_id)
