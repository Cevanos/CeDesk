"""The Slack bot of the `slack` app (md X5): posts in a channel or a thread, and reads the
names of whoever replied or was mentioned, and of a shared file. Rules in domain/slack.py.

Every call goes through `AppPost` (the egress guard, the checked address pinned, https,
no redirects, at most 10 s), to slack.com only; the token is only in the Authorization
header, and the secrets are never logged or shown.
"""

import re
from typing import Any
from uuid import UUID

from cedesk.crypto import CannotOpen, Sealer
from cedesk.db import workspace_session
from cedesk.helpdesk.domain import slack as rules
from cedesk.helpdesk.domain.apps import App
from cedesk.helpdesk.repo import apps as app_repo
from cedesk.helpdesk.service.app_post import AppCallError
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.helpdesk.service.jira import PostFactory

SOMEONE = "Someone in Slack"
_USER = re.compile(r"^[UW][A-Z0-9]{2,39}$")
_CHANNEL = re.compile(r"^[CG][A-Z0-9]{6,29}$")
_TS = re.compile(r"^[0-9]{1,20}\.[0-9]{1,10}$")
_FILE = re.compile(r"^F[A-Z0-9]{2,40}$")


def bound(workspace_id: UUID) -> str:
    """What the app's secrets are sealed to (as every app: `<workspace>:app:<app>`)."""
    return f"{workspace_id}:app:{App.slack.value}"


class Bot:
    def __init__(self, post: PostFactory, token: str, signing_secret: str) -> None:
        self._post = post
        self._token = token
        self.signing_secret = signing_secret

    def _client(self) -> Any:
        return self._post(rules.API_HOST, {"Authorization": f"Bearer {self._token}"}, "Slack")

    async def _call(self, method: str, body: dict[str, Any], where: str) -> dict[str, Any]:
        data = await self._client().post(f"/api/{method}", body)
        problem = rules.api_problem(data, where)
        if problem is not None:
            raise AppCallError(problem[0], permanent=problem[1])
        assert isinstance(data, dict)
        return data

    async def check(self) -> None:
        """At install: Slack knows the token."""
        await self._call("auth.test", {}, "")

    async def say(
        self, channel: str, text: str, *, thread_ts: str | None = None
    ) -> tuple[str, str]:
        """Posts; returns (channel id, ts) of the new message."""
        body: dict[str, Any] = {"channel": channel, "text": text, "unfurl_links": False,
                                "unfurl_media": False}  # fmt: skip
        if thread_ts is not None:
            body["thread_ts"] = thread_ts
        data = await self._call("chat.postMessage", body, channel)
        channel_id, ts = data.get("channel"), data.get("ts")
        if not (isinstance(channel_id, str) and _CHANNEL.match(channel_id)
                and isinstance(ts, str) and _TS.match(ts)):  # fmt: skip
            raise AppCallError("Slack answered without the message's id.", permanent=False)
        return channel_id, ts

    async def name_of(self, user: str) -> str | None:
        """The person's Slack name; None if Slack can't say."""
        if not _USER.match(user):
            return None
        try:
            data = await self._client().get(f"/api/users.info?user={user}")
        except AppCallError:
            return None
        if rules.api_problem(data, "") is not None:
            return None
        person = data.get("user") if isinstance(data, dict) else None
        profile = person.get("profile") if isinstance(person, dict) else None
        names = [profile.get("display_name"), profile.get("real_name")] if isinstance(
            profile, dict) else []  # fmt: skip
        names.append(person.get("name") if isinstance(person, dict) else None)
        found = next((n for n in names if isinstance(n, str) and n.strip()), None)
        return found.strip()[:120] if found else None

    async def file_name(self, file_id: str) -> str | None:
        """A shared file's name, when Slack's event didn't carry it (files.info, scope
        files:read; the file itself isn't fetched). None if Slack can't say."""
        if not _FILE.match(file_id):
            return None
        try:
            data = await self._client().get(f"/api/files.info?file={file_id}")
        except AppCallError:
            return None
        if rules.api_problem(data, "") is not None:
            return None  # e.g. missing_scope: the app was made before files:read
        return rules.file_name(data.get("file") if isinstance(data, dict) else None)


def stand_in(user: str) -> str:
    """Who a reply is from until Slack says the person's name: their Slack id."""
    return f"Slack user {user}" if _USER.match(user) else SOMEONE


async def load(
    sm: Sessionmaker, sealer: Sealer, post: PostFactory, workspace_id: UUID
) -> Bot | None:
    """The workspace's bot, or None when the app isn't installed (or can't be opened)."""
    async with workspace_session(sm, workspace_id) as s:
        row = await app_repo.get(s, App.slack.value)
    if row is None:
        return None
    try:
        value = sealer.open(bytes(row["secret_sealed"]), bound_to=bound(workspace_id))
        token, signing_secret = rules.opened(value)
    except (CannotOpen, rules.BadSlack):
        return None
    return Bot(post, token, signing_secret)
