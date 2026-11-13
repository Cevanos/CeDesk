"""Push notifications to the mobile app (md P8). Rules in domain/push.py; sending in
cedesk/push_client.py (Firebase Cloud Messaging).

- Devices: anyone signed in who reads tickets registers their phone's token, lists their
  own devices (never the token itself, only its end) and removes one. Nobody sees or
  removes another person's device.
- `push_due` (the worker, every pass): in each workspace, notifications not looked at yet
  are claimed with SKIP LOCKED and marked handled in the same transaction, so several
  workers never send a banner twice (a crash loses a banner rather than doubling it; the
  bell still has it). Then, outside the transaction, each banner kind no older than 10
  minutes goes to the person's devices. Tokens the push service says are gone are
  deleted. With push off (no settings), notifications are only marked handled.
- A refused key stops the pass and is logged (the reason only, never the key or a token).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import RowMapping

from cedesk.db import workspace_session
from cedesk.helpdesk.domain import push as rules
from cedesk.helpdesk.domain.auth import Caller
from cedesk.helpdesk.domain.permissions import Action
from cedesk.helpdesk.domain.tickets import label
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import push as repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.errors import Invalid, NotFound
from cedesk.helpdesk.service.members import require
from cedesk.push_client import PushError, Sent

logger = logging.getLogger("cedesk.push")


class Sender(Protocol):
    async def send(
        self, device_token: str, title: str, body: str, data: dict[str, str]
    ) -> Sent: ...


@dataclass(frozen=True)
class Device:
    id: UUID
    platform: str
    token_hint: str
    created_at: datetime
    last_seen_at: datetime


def _device(r: RowMapping) -> Device:
    return Device(r["id"], str(r["platform"]), rules.hint(str(r["token"])),
                  r["created_at"], r["last_seen_at"])  # fmt: skip


async def register(sm: Sessionmaker, caller: Caller, *, platform: str, token: str) -> Device:
    require(caller, Action.read_ticket)
    try:
        clean = rules.check_token(token)
        kind = rules.Platform(platform)
    except (rules.BadToken, ValueError) as exc:
        raise Invalid(str(exc) if isinstance(exc, rules.BadToken) else
                      "platform is ios or android.") from exc  # fmt: skip
    async with workspace_session(sm, caller.workspace_id) as s:
        device_id = await repo.register(s, caller.workspace_id, caller.member_id, kind.value, clean)
        await repo.keep_newest(s, caller.member_id, rules.MAX_DEVICES)
        rows = await repo.devices_of(s, caller.member_id)
    return next(_device(r) for r in rows if r["id"] == device_id)


async def devices(sm: Sessionmaker, caller: Caller) -> list[Device]:
    require(caller, Action.read_ticket)
    async with workspace_session(sm, caller.workspace_id) as s:
        return [_device(r) for r in await repo.devices_of(s, caller.member_id)]


async def unregister(sm: Sessionmaker, caller: Caller, device_id: UUID) -> None:
    """Your own device only (another person's answers 404, like one that doesn't exist)."""
    async with workspace_session(sm, caller.workspace_id) as s:
        if not await repo.remove(s, caller.member_id, device_id):
            raise NotFound("Device not found.")


async def push_due(sm: Sessionmaker, sender: Sender | None, *, now: datetime | None = None) -> int:
    """Returns how many banners were sent, in every workspace."""
    now = now or datetime.now(UTC)
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    sent = 0
    for workspace_id in workspace_ids:
        try:
            sent += await _workspace(sm, sender, workspace_id, now)
        except PushError as exc:
            if not exc.config:
                raise
            logger.error("push stopped: %s", exc, extra={"event": "push"})
            return sent
    return sent


async def _workspace(
    sm: Sessionmaker, sender: Sender | None, workspace_id: UUID, now: datetime
) -> int:
    sent = 0
    while True:
        async with workspace_session(sm, workspace_id) as s:
            claimed = await repo.claim(s, rules.BATCH)
            wanted = [
                n for n in claimed
                if sender is not None and n["kind"] in rules.BANNERS
                and now - n["created_at"] <= rules.MAX_AGE
            ]  # fmt: skip
            if wanted:
                by_member, numbers = await repo.targets(
                    s,
                    list({n["member_id"] for n in wanted}),
                    list({n["ticket_id"] for n in wanted}),
                )
        if wanted and sender is not None:
            gone: list[str] = []
            for n in wanted:
                number = numbers.get(n["ticket_id"])
                shown = rules.banner(str(n["kind"]), label(number)) if number else None
                if shown is None:
                    continue  # the ticket is gone since
                data = {"ticket_id": str(n["ticket_id"]), "notification_id": str(n["id"]),
                        "kind": str(n["kind"])}  # fmt: skip
                for device in by_member.get(n["member_id"], []):
                    result = await sender.send(str(device["token"]), shown[0], shown[1], data)
                    if result.ok:
                        sent += 1
                    elif result.gone:
                        gone.append(str(device["token"]))
            if gone:
                async with workspace_session(sm, workspace_id) as s:
                    await repo.remove_tokens(s, gone)
        if len(claimed) < rules.BATCH:
            return sent
