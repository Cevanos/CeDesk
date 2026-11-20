"""API keys for other systems (md PL1, PL2, C3), the rules that need no database.

- A key is `cdk_<workspace id, 32 hex>_<secret>`. The workspace part only says where to
  look; the whole key must match a stored SHA-256 hash in that workspace, so editing it
  gains nothing. The key is shown once, when it is made; CeDesk keeps its hash and last 4
  characters. 256 random bits, so a fast hash is enough (no password stretching).
- A key acts as the Admin who made it, as Zendesk's and Freshdesk's API tokens act as
  their user, limited by its access level. System lines and the audit log say
  "<name> (API key "<key name>")". A key stops working when that member is deactivated.
- Where a key may go is a fixed list (`ROUTES`). Signing in, passwords, 2FA, members,
  roles, security, SSO, Cevanos, imports, privacy and API keys themselves need a person.
- Levels: `read` (GET only), `read_write` (tickets, customers, notes, tags, tasks, time,
  customer messages in), `admin` (also the settings on the list: teams, ticket types,
  saved replies, macros, rules, help center). No key approves anything (md D10).
"""

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from cedesk.helpdesk.domain.permissions import Action

PREFIX = "cdk_"
_HEX = 32  # a UUID without dashes
SECRET_BYTES = 32
RATE_PER_MINUTE = 700  # per workspace, all its keys together (md PL2)
NAME_MAX = 80
API_PREFIX = "/api/v1/"


class KeyAccess(StrEnum):
    read = "read"
    read_write = "read_write"
    admin = "admin"


_ORDER = {KeyAccess.read: 0, KeyAccess.read_write: 1, KeyAccess.admin: 2}


def new_key(workspace_id: UUID) -> str:
    return f"{PREFIX}{workspace_id.hex}_{secrets.token_urlsafe(SECRET_BYTES)}"


def is_key(token: str) -> bool:
    return token.startswith(PREFIX)


def key_workspace(key: str) -> UUID | None:
    """The workspace a key names, or None when it isn't shaped like a key."""
    if not is_key(key) or len(key) > 200:
        return None
    head, sep, secret = key[len(PREFIX) :].partition("_")
    if len(head) != _HEX or not sep or not secret:
        return None
    try:
        return UUID(hex=head)
    except ValueError:
        return None


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def last4(key: str) -> str:
    return key[-4:]


def hint(last: str) -> str:
    """How a key is shown after it was made."""
    return f"{PREFIX}…{last}"


# What each level may do at most; the creator's own rights limit it further.
_READ = frozenset({Action.read_ticket, Action.list_members, Action.reports})
_NEVER = frozenset(
    {Action.manage_access, Action.approve, Action.export_contacts, Action.manage_campaigns}
)
_CAP: dict[KeyAccess, frozenset[Action]] = {
    KeyAccess.read: _READ,
    # Contact import and merge change many customers at once: admin keys only.
    KeyAccess.read_write: frozenset(Action)
    - _NEVER
    - {Action.settings, Action.manage_contacts, Action.merge_contacts},
    KeyAccess.admin: frozenset(Action) - _NEVER,
}


def capped(level: KeyAccess, actions: frozenset[Action]) -> frozenset[Action]:
    """The creator's rights, limited to what the key's level allows."""
    return actions & _CAP[level]


def can_make(level: KeyAccess, creator_actions: frozenset[Action]) -> bool:
    """An admin key needs a creator with `settings`; any key needs `manage_access`."""
    if Action.manage_access not in creator_actions:
        return False
    return level is not KeyAccess.admin or Action.settings in creator_actions


@dataclass(frozen=True)
class Route:
    """`write`: the lowest level a write (anything but GET) needs; None: reads only."""

    write: KeyAccess | None


ROUTES: dict[str, Route] = {
    "tickets": Route(KeyAccess.read_write),
    "tasks": Route(KeyAccess.read_write),
    "time": Route(KeyAccess.read_write),
    "contacts": Route(KeyAccess.read_write),
    "inbound/tickets": Route(KeyAccess.read_write),
    "reports": Route(None),
    "me": Route(None),  # who the key acts as (`/me` itself; nothing under it)
    "members": Route(None),
    "agents": Route(None),
    "attachments": Route(None),
    "files": Route(None),
    "teams": Route(KeyAccess.admin),
    "ticket-types": Route(KeyAccess.admin),
    "saved-replies": Route(KeyAccess.admin),
    "macros": Route(KeyAccess.admin),
    "rules": Route(KeyAccess.admin),
    "article-categories": Route(KeyAccess.admin),
    "articles": Route(KeyAccess.admin),
}
"""Route groups: the path after /api/v1/, up to the next `/` (or the two segments for
`inbound/tickets`, since `/inbound/email` belongs to the mail service)."""

_READS = frozenset({"GET", "HEAD"})


def _group(path: str) -> str | None:
    if not path.startswith(API_PREFIX):
        return None
    parts = path[len(API_PREFIX) :].rstrip("/").split("/")
    if parts[0] == "inbound":
        return "inbound/tickets" if len(parts) > 1 and parts[1] == "tickets" else "inbound"
    if parts[0] == "me" and len(parts) > 1:
        return "me/*"  # 2FA, password, status: a person's own
    return parts[0]


def refusal(level: KeyAccess, method: str, path: str) -> str | None:
    """Why a key at `level` can't make this request, or None when it can."""
    group = _group(path)
    route = ROUTES.get(group) if group is not None else None
    if route is None:
        return "An API key can't use this part of the API. Sign in as a person for it."
    if method.upper() in _READS:
        return None
    if level is KeyAccess.read:
        return "This API key can only read."
    if route.write is None or _ORDER[level] < _ORDER[route.write]:
        return "This API key's access level doesn't allow this change."
    return None
