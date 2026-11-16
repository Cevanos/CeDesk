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

import httpx

LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# source -> the vendor domain a workspace's name is put in front of
ACCOUNT_DOMAINS: Mapping[str, str] = {
    "zendesk": "zendesk.com",
    "freshdesk": "freshdesk.com",
    "gorgias": "gorgias.com",
    "shopify": "myshopify.com",  # the Shopify app (md PL7): <shop>.myshopify.com
    "jira": "atlassian.net",  # the Jira app (md X10): <site>.atlassian.net
}
# sources whose host is fixed (nothing the Admin types reaches it)
FIXED_HOSTS: Mapping[str, Mapping[str, str]] = {
    "intercom": {
        "us": "api.intercom.io",
        "eu": "api.eu.intercom.io",
        "au": "api.au.intercom.io",
    },
    "helpscout": {"api": "api.helpscout.net", "docs": "docsapi.helpscout.net"},
    "hubspot": {"api": "api.hubapi.com"},
    # Zoho Desk: the API and the sign-in service of each data centre (md 8.3)
    "zoho": {
        "us": "desk.zoho.com",
        "eu": "desk.zoho.eu",
        "in": "desk.zoho.in",
        "au": "desk.zoho.com.au",
        "jp": "desk.zoho.jp",
        "accounts-us": "accounts.zoho.com",
        "accounts-eu": "accounts.zoho.eu",
        "accounts-in": "accounts.zoho.in",
        "accounts-au": "accounts.zoho.com.au",
        "accounts-jp": "accounts.zoho.jp",
    },
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


class PinnedTransport(httpx.AsyncBaseTransport):
    """Connects only to an address it has just checked. The guard looks a host up and checks
    it, but a plain client would look it up again when it connects, and a host whose
    answers change between the two (DNS rebinding) could then reach the server's own
    network. Here each request is resolved once, every address must be public, and the
    connection goes to that address, with the host name kept for TLS (SNI and the
    certificate check) and in the Host header. A refusal is an `httpx.ConnectError`.

    Every outbound call a workspace can point (webhooks, imports, apps) uses it; tests pass
    their own transport instead."""

    def __init__(self, resolver: Resolver = resolve) -> None:
        self._resolver = resolver
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        addresses = list(await self._resolver(host))
        if not addresses or not all(is_public(a) for a in addresses):
            raise httpx.ConnectError(f"'{host}' isn't a public address.", request=request)
        request.extensions = {**request.extensions, "sni_hostname": host}
        url = request.url
        # IPv4 first (a container often has no IPv6 route), then the next checked address
        # when one can't be reached: never an address that wasn't checked above.
        ordered = sorted(addresses, key=lambda a: ":" in a)
        for i, address in enumerate(ordered):
            request.url = url.copy_with(host=address.split("%", 1)[0])
            try:
                return await self._inner.handle_async_request(request)
            except httpx.ConnectError:
                if i == len(ordered) - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        await self._inner.aclose()


def pinned(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncBaseTransport:
    """The transport to use: the one given (tests), else a `PinnedTransport`."""
    return transport if transport is not None else PinnedTransport()
