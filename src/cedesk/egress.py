"""Where an import may connect (md 8.2 step 1, section 12 rule 5: every address a workspace
typed goes through the egress guard).

An Admin types a Zendesk subdomain or a Freshdesk domain; the server then calls it. Left
free, that is a way to make this server call anything it can reach (a cloud metadata
address, a database on the private network). Two rules keep it to the help desk:

1. **The host is built, never taken.** The Admin gives a name (`acme`); the host is
   `acme.zendesk.com`, the vendor's own domain. A full address they paste is reduced to its
   name first, and anything that isn't a plain DNS label (a dot, a slash, `@`, `#`, a port,
   an IP address) is refused. Only https, and a redirect is never followed.
2. **The address it resolves to must be a public one.** Before connecting, the host is
   resolved and every address must be globally routable: not private, loopback, link-local
   (169.254.169.254 is the cloud metadata address), multicast, reserved or unspecified, and
   an IPv6 address that wraps one of those (`::ffff:10.0.0.1`) counts as it.

Nothing here knows about any one help desk's API; `service/imports/http.py` uses it.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence

LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# source -> the vendor domain a workspace's name is put in front of
ACCOUNT_DOMAINS: Mapping[str, str] = {
    "zendesk": "zendesk.com",
    "freshdesk": "freshdesk.com",
    "gorgias": "gorgias.com",
    "shopify": "myshopify.com",  # the Shopify app (md PL7): <shop>.myshopify.com
}
# sources whose host is fixed (nothing the Admin types reaches it)
FIXED_HOSTS: Mapping[str, Mapping[str, str]] = {
    "intercom": {
        "us": "api.intercom.io",
        "eu": "api.eu.intercom.io",
        "au": "api.au.intercom.io",
    },
    "helpscout": {"api": "api.helpscout.net", "docs": "docsapi.helpscout.net"},
}


class BadAddress(ValueError):
    """The name or address can't be an account of the help desk. The message is for the Admin."""


def account_label(source: str, raw: str) -> str:
    """The account name from what the Admin typed: `acme`, `Acme`, `acme.zendesk.com` or
    `https://acme.zendesk.com/agent` all give `acme`. Raises `BadAddress`."""
    domain = ACCOUNT_DOMAINS.get(source)
    if domain is None:
        raise BadAddress(f"{source} has no account address to type.")
    value = raw.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if value.endswith("." + domain):
        value = value[: -len(domain) - 1]
    if not LABEL.match(value):
        raise BadAddress(
            f"'{raw.strip()[:60]}' isn't an account name. Use just the part before "
            f".{domain}, for example 'acme'."
        )
    return value


def account_host(source: str, raw: str) -> str:
    """`acme` -> `acme.zendesk.com`."""
    return f"{account_label(source, raw)}.{ACCOUNT_DOMAINS[source]}"


def fixed_host(source: str, which: str) -> str:
    try:
        return FIXED_HOSTS[source][which]
    except KeyError:
        raise BadAddress(f"No such {source} region: {which!r}.") from None


def is_public(address: str) -> bool:
    """Whether a resolved address is one a server may call. False for anything that isn't a
    plain, globally routable address (and for text that isn't an address at all)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


Resolver = Callable[[str], Awaitable[Sequence[str]]]
Guard = Callable[[str], Awaitable[None]]


async def resolve(host: str) -> Sequence[str]:
    """Every address `host` resolves to (the system resolver, off the event loop)."""
    loop = asyncio.get_running_loop()
    try:
        found = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({str(item[4][0]) for item in found})


def guard(resolver: Resolver = resolve) -> Guard:
    """A check to run on a host before connecting to it: raises `BadAddress` unless the host
    resolves, and every address it resolves to is public. `resolver` is replaced in tests."""

    async def check(host: str) -> None:
        addresses = await resolver(host)
        if not addresses:
            raise BadAddress(f"'{host}' can't be found. Check the account name.")
        for address in addresses:
            if not is_public(address):
                raise BadAddress(f"'{host}' isn't a public address. Check the account name.")

    return check


check_public: Guard = guard()
