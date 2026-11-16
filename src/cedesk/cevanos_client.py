"""Calls to Cevanos's public `/v1` API with a workspace's key (md 7.1).

Routes (all but `/v1/me` are what Cevanos still has to add, md 14.19 and section 16):
- `GET  /v1/me`                            check a key
- `GET  /v1/conversations/{id}/messages`  the chat, any channel, oldest first
- `POST /v1/conversations/{id}/take-over` body `{"operator_name"}`: a person has it now
- `POST /v1/conversations/{id}/reply`     body `{"body", "operator_name"}`
- `POST /v1/conversations/{id}/close`
- `GET  /v1/conversations/{id}`           the chat's contact (AI6, `conversations:read`)
- `POST /v1/summaries`                    `{"text"}` -> `{"summary"}` (AI1, `ai:assist`)
- `POST /v1/rewrite`                      `{"text", "tone"}` -> `{"text"}` (AI2)
- `POST /v1/translate`                    `{"text", "to"}` -> `{"text", "from"}` (AI3)
- `POST /v1/drafts`                       `{"text", "agent_id"}` -> `{"text"}` (AI4)
- `GET  /v1/contacts?email=`              contacts with that email (AI5, `contacts:read`)
- `GET  /v1/contacts/{id}/orders`         and `.../payments`: from its stores (AI5)
- `GET  /v1/analytics/summary?from&to`    the AI numbers (AI7, `analytics:read`)
- `POST /v1/governance/holds/{id}/decision` `{"decision", "note", "decided_by"}` (X7)

Every success is `{"data": ...}`; every refusal `{"error": {"code", "message"}}`. Writes
carry `Idempotency-Key` (the outbox effect's key), so a retry after a lost answer is not a
second reply in the customer's chat. The AI calls (summaries, rewrite, translate, drafts)
change nothing in Cevanos but its credits, so they carry no key.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from cedesk.crypto import Sealer
from cedesk.settings import Settings

TIMEOUT_SECONDS = 20
AI_TIMEOUT_SECONDS = 45  # a summary or a draft is written by a model while the agent waits
USER_AGENT = "CeDesk/1 (+cevanos connector)"


class CevanosError(Exception):
    """`permanent`: retrying won't help (no such chat, bad key, refused body). Otherwise
    Cevanos is down, slow or rate-limiting, and the call is tried again later."""

    def __init__(
        self,
        message: str,
        *,
        permanent: bool,
        status: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.status = status
        self.retry_after = retry_after  # seconds, on a 429


class CevanosApi(Protocol):
    async def me(self) -> Any: ...

    async def messages(self, conversation_id: str) -> Any: ...

    async def take_over(self, conversation_id: str, *, operator_name: str, key: str) -> None: ...

    async def reply(
        self, conversation_id: str, *, body: str, operator_name: str, key: str
    ) -> None: ...

    async def close(self, conversation_id: str, *, key: str) -> None: ...

    async def conversation(self, conversation_id: str) -> Any: ...

    async def summarize(self, *, text: str) -> Any: ...

    async def rewrite(self, *, text: str, tone: str) -> Any: ...

    async def translate(self, *, text: str, to: str) -> Any: ...

    async def draft(self, *, text: str, agent_id: str | None) -> Any: ...

    async def contacts(self, *, email: str) -> Any: ...

    async def orders(self, contact_id: str) -> Any: ...

    async def payments(self, contact_id: str) -> Any: ...

    async def analytics(self, *, start: str, end: str) -> Any: ...

    async def hold_decision(
        self, hold_id: str, *, decision: str, note: str | None, decided_by: str, key: str
    ) -> None: ...


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_key: str


def _error(response: httpx.Response) -> CevanosError:
    try:
        detail = response.json().get("error", {})
        code, message = str(detail.get("code", "")), str(detail.get("message", ""))
    except (ValueError, AttributeError):
        code, message = "", ""
    text = f"Cevanos answered {response.status_code}" + (f" {code}" if code else "")
    if message:
        text += f": {message[:200]}"
    # 408 and 429 and 5xx: try later. Other 4xx (bad key, no such chat, refused): won't work.
    temporary = response.status_code in (408, 429) or response.status_code >= 500
    wait = response.headers.get("retry-after", "")
    return CevanosError(
        text,
        permanent=not temporary,
        status=response.status_code,
        retry_after=min(int(wait), 3600) if wait.isdigit() else None,
    )


class HttpCevanos:
    def __init__(
        self, connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base = connection.base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {connection.api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        self._transport = transport

    def _url(self, conversation_id: str, action: str) -> str:
        return f"{self._base}/v1/conversations/{quote(conversation_id, safe='')}/{action}"

    async def _call(
        self,
        method: str,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        key: str | None,
        params: Mapping[str, str] | None = None,
        wait_seconds: float = TIMEOUT_SECONDS,
    ) -> Any:
        headers = dict(self._headers)
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            async with httpx.AsyncClient(
                timeout=wait_seconds, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.request(
                    method, url, json=json, params=params, headers=headers
                )
        except httpx.HTTPError as exc:
            raise CevanosError(
                f"Cevanos can't be reached: {type(exc).__name__}", permanent=False
            ) from None
        if response.status_code >= 300:
            raise _error(response)
        try:
            return response.json() if response.content else None
        except ValueError:
            raise CevanosError("Cevanos sent an answer that isn't JSON.", permanent=False) from None

    async def me(self) -> Any:
        return await self._call("GET", f"{self._base}/v1/me", key=None)

    async def messages(self, conversation_id: str) -> Any:
        return await self._call("GET", self._url(conversation_id, "messages"), key=None)

    async def take_over(self, conversation_id: str, *, operator_name: str, key: str) -> None:
        await self._call(
            "POST",
            self._url(conversation_id, "take-over"),
            json={"operator_name": operator_name},
            key=key,
        )

    async def reply(self, conversation_id: str, *, body: str, operator_name: str, key: str) -> None:
        await self._call(
            "POST",
            self._url(conversation_id, "reply"),
            json={"body": body, "operator_name": operator_name},
            key=key,
        )

    async def close(self, conversation_id: str, *, key: str) -> None:
        await self._call("POST", self._url(conversation_id, "close"), json={}, key=key)

    async def conversation(self, conversation_id: str) -> Any:
        url = f"{self._base}/v1/conversations/{quote(conversation_id, safe='')}"
        return await self._call("GET", url, key=None)

    async def _ai(self, path: str, body: Mapping[str, Any]) -> Any:
        url = f"{self._base}/v1/{path}"
        return await self._call("POST", url, json=body, key=None, wait_seconds=AI_TIMEOUT_SECONDS)

    async def summarize(self, *, text: str) -> Any:
        return await self._ai("summaries", {"text": text})

    async def rewrite(self, *, text: str, tone: str) -> Any:
        return await self._ai("rewrite", {"text": text, "tone": tone})

    async def translate(self, *, text: str, to: str) -> Any:
        return await self._ai("translate", {"text": text, "to": to})

    async def draft(self, *, text: str, agent_id: str | None) -> Any:
        return await self._ai("drafts", {"text": text, "agent_id": agent_id})

    async def contacts(self, *, email: str) -> Any:
        return await self._call("GET", f"{self._base}/v1/contacts", key=None,
                                params={"email": email})  # fmt: skip

    def _contact(self, contact_id: str, what: str) -> str:
        return f"{self._base}/v1/contacts/{quote(contact_id, safe='')}/{what}"

    async def orders(self, contact_id: str) -> Any:
        return await self._call("GET", self._contact(contact_id, "orders"), key=None)

    async def payments(self, contact_id: str) -> Any:
        return await self._call("GET", self._contact(contact_id, "payments"), key=None)

    async def analytics(self, *, start: str, end: str) -> Any:
        return await self._call("GET", f"{self._base}/v1/analytics/summary", key=None,
                                params={"from": start, "to": end})  # fmt: skip

    async def hold_decision(
        self, hold_id: str, *, decision: str, note: str | None, decided_by: str, key: str
    ) -> None:
        url = f"{self._base}/v1/governance/holds/{quote(hold_id, safe='')}/decision"
        body = {"decision": decision, "note": note, "decided_by": decided_by}
        await self._call("POST", url, json=body, key=key)


Connect = Callable[[Connection], CevanosApi]


@dataclass(frozen=True)
class Cevanos:
    """The connector for this deployment: Cevanos's address, the sealer for the keys kept
    in the database, and how a key becomes a client (a fake one in tests)."""

    base_url: str
    sealer: Sealer
    connect: Connect

    def api(self, api_key: str) -> CevanosApi:
        return self.connect(Connection(self.base_url, api_key))


def make_cevanos(settings: Settings, connect: Connect | None = None) -> Cevanos:
    return Cevanos(
        base_url=settings.cevanos_url.strip().rstrip("/"),
        sealer=Sealer(settings.secret_key.get_secret_value()),
        connect=connect or HttpCevanos,
    )
