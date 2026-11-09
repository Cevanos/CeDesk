"""Files on tickets: upload, list, a signed download link, and the local-disk download."""

import asyncio
import time
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from cedesk.helpdesk.api.deps import CallerDep, SessionmakerDep, StoreDep
from cedesk.helpdesk.domain.paging import DEFAULT_LIMIT, MAX_LIMIT
from cedesk.helpdesk.service import attachments
from cedesk.helpdesk.service.errors import Gone, Invalid, NotFound
from cedesk.storage import LocalStore, content_disposition
from cedesk.storage import links as file_links

router = APIRouter(tags=["attachments"])

# A browser form posts its fields wrapped around the file; the upload is the file itself.
_FORM_TYPES = ("multipart/", "application/x-www-form-urlencoded")


class AttachmentOut(BaseModel):
    id: UUID
    ticket_id: UUID
    message_id: UUID | None = Field(description="The reply or note it was sent with, if any.")
    name: str
    size: int = Field(description="Bytes.")
    mime: str
    scan_status: str = Field(description="not_scanned, clean or infected.")
    uploaded_by: UUID | None
    created_at: datetime


class AttachmentPageOut(BaseModel):
    attachments: list[AttachmentOut] = Field(description="Newest first.")
    next_cursor: str | None = Field(description="Send as `cursor` for older files.")


class DownloadOut(BaseModel):
    url: str = Field(description="Opens the file without signing in, until `expires_at`.")
    expires_at: datetime
    attachment: AttachmentOut


async def _read_file(request: Request, max_bytes: int) -> bytes:
    """The request body, refused as soon as it passes `max_bytes`, so an oversized upload is
    never held in full."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise attachments.too_large(max_bytes)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > max_bytes:
            raise attachments.too_large(max_bytes)
    return bytes(data)


@router.post(
    "/tickets/{ticket_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a file to a ticket",
    description=(
        "The request body is the file itself (not a form), with its type in `Content-Type`, "
        "for example `fetch(url, {method: 'POST', body: file, headers: {'Content-Type': "
        "file.type}})`. At most 25 MB by default (`CEDESK_ATTACHMENT_MAX_BYTES`): 413 above."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={413: {"description": "The file is larger than the limit."}},
)
async def upload(
    ticket_id: UUID,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    store: StoreDep,
    name: Annotated[
        str, Query(min_length=1, max_length=1000, description="The file's name, e.g. photo.jpg.")
    ],
) -> AttachmentOut:
    content_type = request.headers.get("content-type")
    if content_type and content_type.lower().startswith(_FORM_TYPES):
        raise Invalid("Send the file itself as the request body, not a form.")
    max_bytes: int = request.app.state.settings.attachment_max_bytes
    data = await _read_file(request, max_bytes)
    row = await attachments.upload(
        sm,
        store,
        caller,
        ticket_id,
        name=name,
        mime=content_type,
        data=data,
        max_bytes=max_bytes,
    )
    return AttachmentOut.model_validate(dict(row))


@router.get(
    "/tickets/{ticket_id}/attachments",
    response_model=AttachmentPageOut,
    summary="A ticket's files, newest first",
)
async def list_attachments(
    ticket_id: UUID,
    caller: CallerDep,
    sm: SessionmakerDep,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> AttachmentPageOut:
    page = await attachments.ticket_attachments(sm, caller, ticket_id, cursor=cursor, limit=limit)
    return AttachmentPageOut(
        attachments=[AttachmentOut.model_validate(dict(r)) for r in page.attachments],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/attachments/{attachment_id}",
    response_model=DownloadOut,
    summary="A short-lived link to download a file",
    description="Ask again for a new link once it expires (5 minutes by default).",
)
async def download_link(
    attachment_id: UUID,
    request: Request,
    caller: CallerDep,
    sm: SessionmakerDep,
    store: StoreDep,
) -> DownloadOut:
    seconds: int = request.app.state.settings.download_link_seconds
    found = await attachments.download_link(sm, store, caller, attachment_id, seconds=seconds)
    url = found.url
    if url.startswith("/"):  # local disk: a path on this API
        url = str(request.base_url).rstrip("/") + url
    return DownloadOut(
        url=url,
        expires_at=found.expires_at,
        attachment=AttachmentOut.model_validate(dict(found.attachment)),
    )


@router.get(
    "/files/{token}",
    response_class=FileResponse,
    summary="Download a file with a signed link (local storage only)",
    description="The `url` from GET /attachments/{id}. No sign-in: the link is the permission.",
    responses={404: {"description": "Not a valid link."}, 410: {"description": "Expired."}},
)
async def download_file(
    token: Annotated[str, Path(max_length=4096)], store: StoreDep
) -> FileResponse:
    if not isinstance(store, LocalStore):
        raise NotFound("File not found.")
    now = int(time.time())
    try:
        link = file_links.verify(token, store.link_key, now=now)
        path = store.path(link.key)
    except file_links.ExpiredLink as exc:
        raise Gone(str(exc)) from exc
    except ValueError as exc:  # BadLink, or a key no store would make
        raise NotFound("File not found.") from exc
    if not await asyncio.to_thread(path.is_file):
        raise NotFound("File not found.")
    return FileResponse(
        path,
        media_type=link.content_type,
        headers={
            "Content-Disposition": content_disposition(link.name),
            # Even a file that claims to be HTML is never sniffed or run as a page here.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": f"private, max-age={max(0, link.expires_at - now)}",
        },
    )
