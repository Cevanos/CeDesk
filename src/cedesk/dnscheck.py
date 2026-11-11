"""Reading TXT records for the email address check (md C10). The service takes any
`TxtLookup`; tests pass a fake one."""

from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

TIMEOUT_SECONDS = 5.0


class LookupFailed(Exception):
    """DNS didn't answer (timeout, no name server); not the same as "no record"."""


class TxtLookup(Protocol):
    async def txt(self, name: str) -> list[str]:
        """The TXT records at `name`, each joined into one string; [] when there are none."""
        ...


class DnsLookup:
    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    async def txt(self, name: str) -> list[str]:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = self.timeout
        try:
            answer = await resolver.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except (dns.exception.DNSException, OSError) as exc:
            raise LookupFailed(str(exc) or exc.__class__.__name__) from exc
        return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]
