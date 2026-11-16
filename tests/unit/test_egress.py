"""The egress guard (md 8.2 step 1, section 12 rule 5): the host is built from a plain account
name on the vendor's own domain, and every address it resolves to must be a public one."""

import asyncio
from collections.abc import Sequence

import pytest

from cedesk import egress
from cedesk.egress import BadAddress


@pytest.mark.parametrize(
    "typed",
    [
        "acme",
        "Acme",
        "  acme  ",
        "acme.zendesk.com",
        "ACME.ZENDESK.COM",
        "https://acme.zendesk.com",
        "https://acme.zendesk.com/agent/tickets/1?x=1#top",
        "acme/../..",  # a pasted path is dropped, never used
        "http://acme.zendesk.com/",
    ],
)
def test_an_account_name_is_reduced_to_its_label(typed: str) -> None:
    assert egress.account_label("zendesk", typed) == "acme"
    assert egress.account_host("zendesk", typed) == "acme.zendesk.com"


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "evil.com",  # not on the vendor's domain
        "acme.evil.com",
        "acme.zendesk.com.evil.com",
        "acme@evil.com",
        "evil.com#acme.zendesk.com",
        "acme:8080",
        "169.254.169.254",
        "127.0.0.1",
        "[::1]",
        "-acme",
        "acme-",
        "ac me",
        "acme_corp",
        "a" * 64,  # a label is at most 63 characters
        "ac\\me",
    ],
)
def test_anything_that_is_not_a_plain_account_name_is_refused(typed: str) -> None:
    with pytest.raises(BadAddress):
        egress.account_label("zendesk", typed)


def test_a_name_that_looks_like_localhost_is_still_only_a_vendor_subdomain() -> None:
    # The host is always built on the vendor's domain, so this is a public name, not a loopback.
    assert egress.account_host("zendesk", "localhost") == "localhost.zendesk.com"


def test_each_vendor_has_its_own_domain_and_a_source_with_none_is_refused() -> None:
    assert egress.account_host("freshdesk", "shop") == "shop.freshdesk.com"
    assert egress.account_host("gorgias", "shop.gorgias.com") == "shop.gorgias.com"
    # An account of one vendor is not an account of another.
    with pytest.raises(BadAddress):
        egress.account_label("freshdesk", "shop.zendesk.com")
    with pytest.raises(BadAddress):
        egress.account_label("intercom", "acme")


def test_fixed_hosts_ignore_anything_typed() -> None:
    assert egress.fixed_host("intercom", "us") == "api.intercom.io"
    assert egress.fixed_host("intercom", "eu") == "api.eu.intercom.io"
    assert egress.fixed_host("helpscout", "docs") == "docsapi.helpscout.net"
    with pytest.raises(BadAddress):
        egress.fixed_host("intercom", "evil.com")


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "127.0.0.1",
        "127.1.2.3",
        "0.0.0.0",  # noqa: S104
        "169.254.169.254",  # the cloud metadata address
        "169.254.0.1",
        "100.64.0.1",  # carrier-grade NAT
        "224.0.0.1",  # multicast
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",  # link-local
        "fc00::1",  # unique local
        "fd12:3456::1",
        "ff02::1",  # multicast
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "fe80::1%eth0",  # a zone id doesn't make it public
        "not an address",
        "",
    ],
)
def test_private_loopback_link_local_and_odd_addresses_are_not_public(address: str) -> None:
    assert egress.is_public(address) is False


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "1.1.1.1", "104.16.0.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_are_public(address: str) -> None:
    assert egress.is_public(address) is True


def resolver(*addresses: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str) -> Sequence[str]:
        return list(addresses)

    return resolve


def check(host: str, *addresses: str) -> None:
    asyncio.run(egress.guard(resolver(*addresses))(host))


def test_a_host_that_resolves_to_a_public_address_passes() -> None:
    check("acme.zendesk.com", "104.16.1.1", "104.16.1.2")


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("::1",),
        ("104.16.1.1", "10.0.0.5"),  # one bad address among good ones is enough to refuse
        ("::ffff:192.168.0.1",),
    ],
)
def test_a_host_that_resolves_to_a_private_address_is_refused(addresses: tuple[str, ...]) -> None:
    with pytest.raises(BadAddress, match="isn't a public address"):
        check("acme.zendesk.com", *addresses)


def test_a_host_that_does_not_resolve_is_refused() -> None:
    with pytest.raises(BadAddress, match="can't be found"):
        check("nothing.zendesk.com")


async def test_the_pinned_transport_connects_to_the_address_it_checked() -> None:
    """DNS rebinding: the host is looked up once, every address must be public, and the
    connection goes to that address with the host name kept for TLS and Host."""
    import httpx

    from cedesk.egress import PinnedTransport

    answers = {
        "hooks.example.com": ["93.184.216.34"],
        "rebind.example.com": ["93.184.216.34", "127.0.0.1"],
        "v6.example.com": ["2606:2800:220:1:248:1893:25c8:1946"],
    }

    async def resolver(host: str) -> list[str]:
        return answers.get(host, [])

    seen: list[httpx.Request] = []
    pinned = PinnedTransport(resolver)
    pinned._inner = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204))  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=pinned) as client:
        r = await client.post("https://hooks.example.com/x?a=1", content=b"{}")
        assert r.status_code == 204
        await client.get("https://v6.example.com/")
        for bad in ("https://rebind.example.com/", "https://nowhere.example.com/"):
            try:
                await client.get(bad)
            except httpx.ConnectError as exc:
                assert "isn't a public address" in str(exc)
            else:
                raise AssertionError(bad)
    first, v6 = seen
    assert (first.url.host, first.url.path, first.url.query) == ("93.184.216.34", "/x", b"a=1")
    assert first.headers["Host"] == "hooks.example.com"
    assert first.extensions["sni_hostname"] == "hooks.example.com"
    assert v6.url.host == "2606:2800:220:1:248:1893:25c8:1946"


async def test_the_pinned_transport_prefers_ipv4_and_tries_the_next_address() -> None:
    """A host with IPv6 and IPv4 answers (sorted, the IPv6 one comes first) from a server
    with no IPv6 route: the IPv4 address is used; an address that can't be reached is
    skipped for the next checked one; when none can be reached, the error is raised."""
    import httpx
    import pytest

    from cedesk.egress import PinnedTransport

    answers = {
        "files.example.com": ["2600:9000:208a::1", "65.8.131.128"],
        "two.example.com": ["93.184.216.34", "93.184.216.35"],
        "down.example.com": ["93.184.216.36"],
    }
    dead = {"93.184.216.34", "93.184.216.36", "2600:9000:208a::1"}

    async def resolver(host: str) -> list[str]:
        return answers.get(host, [])

    tried: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        tried.append(r.url.host)
        if r.url.host in dead:
            raise httpx.ConnectError("Network is unreachable", request=r)
        return httpx.Response(200)

    pinned = PinnedTransport(resolver)
    pinned._inner = httpx.MockTransport(handler)  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=pinned) as client:
        assert (await client.get("https://files.example.com/a.pdf")).status_code == 200
        assert tried == ["65.8.131.128"]
        tried.clear()
        assert (await client.get("https://two.example.com/")).status_code == 200
        assert tried == ["93.184.216.34", "93.184.216.35"]
        with pytest.raises(httpx.ConnectError):
            await client.get("https://down.example.com/")
