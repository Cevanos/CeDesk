"""Custom domain for the help center (feature-gap plan 4.2): rules that need no database.

A workspace points a domain it owns (say `help.example.com`) at the help center. Before the
platform serves or issues a certificate for it, the Admin proves control by publishing a TXT
record that holds a token only this workspace was given.
"""

import re
import secrets
from collections.abc import Iterable
from urllib.parse import urlsplit

DOMAIN_MIN = 4  # the table's CHECK: a.bc is the shortest
DOMAIN_MAX = 253
FAILURES_BEFORE_DEMOTION = 3  # consecutive failed checks of a verified domain
VERIFY_PREFIX = "_cedesk-verify"

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class BadDomain(ValueError):
    pass


def clean_domain(value: str) -> str:
    """A host name, lower-case, with international names in their `xn--` form. Refused:
    addresses (IPv4 or IPv6), ports, paths, wildcards, user names and single-label names."""
    text = value.strip().lower().rstrip(".")
    if not text:
        raise BadDomain("Give the domain, for example help.example.com.")
    if any(ch in text for ch in ":/@*?#\\ _") or "." not in text:
        raise BadDomain(
            "Give just the domain, for example help.example.com: no https://, port, path, "
            "wildcard or address."
        )
    try:
        ascii_name = text.encode("idna").decode("ascii")
    except UnicodeError:
        raise BadDomain("That is not a valid domain name.") from None
    labels = ascii_name.split(".")
    sized = DOMAIN_MIN <= len(ascii_name) <= DOMAIN_MAX
    if not sized or not all(_LABEL.match(label) for label in labels):
        raise BadDomain("That is not a valid domain name.")
    if labels[-1].isdigit():  # 192.168.0.1 and the like
        raise BadDomain("Give a domain name, not an IP address.")
    return ascii_name


def hosts_of(urls: Iterable[str]) -> set[str]:
    """The host names in some settings URLs (empty and malformed ones are skipped)."""
    found: set[str] = set()
    for url in urls:
        try:
            host = urlsplit(url).hostname
        except ValueError:
            continue
        if host:
            found.add(host.lower())
    return found


def is_reserved(domain: str, reserved: Iterable[str]) -> bool:
    """True when the domain is one of this platform's own hosts or sits under one: a
    customer must never point `app.example.com` (or anything below it) at a workspace."""
    return any(domain == host or domain.endswith("." + host) for host in reserved)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def txt_name(domain: str) -> str:
    return f"{VERIFY_PREFIX}.{domain}"


def txt_value(token: str) -> str:
    return f"cedesk-verify={token}"


def token_present(records: Iterable[str], token: str) -> bool:
    """True when one of the TXT records at `txt_name` holds this workspace's token."""
    expected = txt_value(token)
    return any(record.strip().strip('"').strip() == expected for record in records)
