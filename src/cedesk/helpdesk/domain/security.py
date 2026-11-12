"""Workspace security settings and the security score (md S3-S6, S8). Pure rules.

One row per workspace; a workspace without one uses `DEFAULTS`, which match how CeDesk
behaved before these settings existed (10-character passwords, 12-hour sessions, no IP
list), so nobody is signed out or locked out by the upgrade.
"""

import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

PASSWORD_MIN_RANGE = (8, 64)
EXPIRY_DAYS = (90, 180)
IP_LIST_MAX = 100
# An idle session also ends this long after sign-in, however busy it is.
IDLE_SESSION_MAX = timedelta(days=7)
# `last_used_at` is written at most this often, so busy agents don't write on every request.
TOUCH_EVERY = timedelta(minutes=1)

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class SessionLength(StrEnum):
    idle_1h = "1h_idle"
    h8 = "8h"
    h12 = "12h"
    d7 = "7d"


_LENGTH = {
    SessionLength.idle_1h: timedelta(hours=1),
    SessionLength.h8: timedelta(hours=8),
    SessionLength.h12: timedelta(hours=12),
    SessionLength.d7: timedelta(days=7),
}


@dataclass(frozen=True)
class Security:
    password_min: int = 10
    password_expiry_days: int | None = None
    session_length: SessionLength = SessionLength.h12
    ip_allowlist: tuple[IpNetwork, ...] = ()
    attachments_need_sign_in: bool = False
    admin_alerts: bool = True
    require_2fa: bool = False


DEFAULTS = Security()


def from_row(row: Mapping[Any, Any] | None) -> Security:
    if row is None:
        return DEFAULTS
    return Security(
        password_min=int(row["password_min"]),
        password_expiry_days=row["password_expiry_days"],
        session_length=SessionLength(row["session_length"]),
        ip_allowlist=tuple(ipaddress.ip_network(n, strict=False) for n in row["ip_allowlist"]),
        attachments_need_sign_in=bool(row["attachments_need_sign_in"]),
        admin_alerts=bool(row["admin_alerts"]),
        require_2fa=bool(row["require_2fa"]),
    )


def session_ends(length: SessionLength, signed_in_at: datetime, used_at: datetime) -> datetime:
    """When a session ends: a fixed time after sign-in, or (idle) an hour after it was last
    used, never later than `IDLE_SESSION_MAX` after sign-in."""
    if length is SessionLength.idle_1h:
        return min(used_at + _LENGTH[length], signed_in_at + IDLE_SESSION_MAX)
    return signed_in_at + _LENGTH[length]


def password_expired(changed_at: datetime, expiry_days: int | None, now: datetime) -> bool:
    return expiry_days is not None and now >= changed_at + timedelta(days=expiry_days)


class BadNetwork(ValueError):
    pass


def parse_networks(entries: Iterable[str]) -> tuple[IpNetwork, ...]:
    """CIDR blocks or single addresses ("203.0.113.0/24", "198.51.100.7", IPv6 too).
    Host bits are cleared ("10.0.0.5/8" -> "10.0.0.0/8"); repeats are dropped."""
    found: list[IpNetwork] = []
    for raw in entries:
        text = raw.strip()
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise BadNetwork(f"{text!r} is not an IP address or CIDR block.") from exc
        if network.prefixlen == 0:
            raise BadNetwork(f"{text} allows every address. Leave the list empty instead.")
        if network not in found:
            found.append(network)
    if len(found) > IP_LIST_MAX:
        raise BadNetwork(f"Up to {IP_LIST_MAX} entries.")
    return tuple(found)


def ip_allowed(ip: str | None, allowlist: Sequence[IpNetwork]) -> bool:
    """An empty list allows everyone. An address that can't be read is refused."""
    if not allowlist:
        return True
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address.version == n.version and address in n for n in allowlist)


def ip_refused(ip: str | None) -> str:
    return f"Your IP address ({ip or 'unknown'}) isn't on this workspace's allowed list."


# The security score (S8): points per item; each item is on or off.
POINTS = {
    "sso": 20,
    "two_factor": 25,
    "ip_allowlist": 15,
    "passwords": 10,
    "redaction": 10,
    "session_length": 5,
    "attachments_sign_in": 5,
    "admin_alerts": 5,
    "portal_domains": 5,
}
STRONG_PASSWORD_MIN = 12


@dataclass(frozen=True)
class ScoreItem:
    key: str
    points: int
    on: bool
    status: str  # healthy, critical (a big item that is off) or recommended


@dataclass(frozen=True)
class Score:
    score: int
    level: str  # healthy (80+), recommended (50-79) or critical (under 50)
    items: list[ScoreItem]


def score(
    settings: Security,
    *,
    sso: bool,
    redaction: bool,
    portal_domains: bool,
) -> Score:
    on = {
        "sso": sso,
        "two_factor": settings.require_2fa,
        "ip_allowlist": bool(settings.ip_allowlist),
        "passwords": settings.password_min >= STRONG_PASSWORD_MIN,
        "redaction": redaction,
        "session_length": settings.session_length is not SessionLength.d7,
        "attachments_sign_in": settings.attachments_need_sign_in,
        "admin_alerts": settings.admin_alerts,
        "portal_domains": portal_domains,
    }
    items = [
        ScoreItem(
            key,
            points,
            on[key],
            "healthy" if on[key] else ("critical" if points >= 15 else "recommended"),
        )
        for key, points in POINTS.items()
    ]
    total = sum(i.points for i in items if i.on)
    level = "healthy" if total >= 80 else ("recommended" if total >= 50 else "critical")
    return Score(total, level, items)


def alert_email(workspace_name: str, what: str, by: str) -> tuple[str, str]:
    """Subject and body of an Admin security alert (S6)."""
    subject = f"Security alert for {workspace_name}: {what}"
    body = (
        f"Hello,\n\n{what}\nDone by: {by}\n\n"
        "If you didn't expect this, check Settings > Members and Settings > Security now.\n\n"
        "You get this because you are an Admin of this CeDesk workspace."
    )
    return subject, body
