"""Checking a support email address (md C10). No database, no network.

Two checks, both for the Admin's peace of mind; neither stops an address from working:
- Forwarding: CeDesk emails the address a one-time code in the subject. When that email
  comes back in (the address forwards to CeDesk, or the mailbox CeDesk reads), the
  address is verified: customers' emails to it reach CeDesk.
- DNS: the address's domain has an SPF record (naming the mail service, when the server
  says which), a DKIM key at the selector the Admin gives, and a DMARC policy. Without
  them, replies from the address land in spam.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

CODE_SUBJECT = "CeDesk forwarding check {code}"
_CODE = re.compile(r"CeDesk forwarding check ([A-Za-z0-9_-]{40,64})")
CODE_LIFETIME = timedelta(days=7)
SELECTOR = re.compile(r"[a-z0-9]([a-z0-9._-]{0,61}[a-z0-9])?")
RECORD_SHOWN = 500  # characters of a DNS record kept for the page


def new_code() -> str:
    return secrets.token_urlsafe(32)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def code_in(subject: str) -> str | None:
    """The forwarding-check code in an email's subject ("Fwd:" and the like around it)."""
    found = _CODE.search(subject or "")
    return found.group(1) if found else None


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


class Check(StrEnum):
    ok = "ok"
    missing = "missing"
    wrong = "wrong"
    not_checked = "not_checked"
    error = "error"  # DNS didn't answer; try again


@dataclass(frozen=True)
class Result:
    status: Check
    record: str | None
    note: str


def _starting(records: list[str], prefix: str) -> list[str]:
    return [r for r in records if r.lower().startswith(prefix)]


SPF_LOOKUPS = 10  # RFC 7208: at most 10 more lookups for one check


def spf_record(records: list[str]) -> str | None:
    found = _starting(records, "v=spf1")
    return found[0] if len(found) == 1 else None


def spf_links(record: str) -> list[str]:
    """The names an SPF record sends the check on to: include:x and redirect=x."""
    links = []
    for term in record.lower().split():
        term = term.lstrip("+-~?")
        for prefix in ("include:", "redirect="):
            if term.startswith(prefix) and "%" not in term:  # macros can't be followed
                links.append(term[len(prefix) :])
    return links


def spf(
    domain: str, records: list[str], include: str | None, reached: frozenset[str] = frozenset()
) -> Result:
    """`include`: the mail service's SPF name (e.g. amazonses.com), when the server knows it.
    `reached`: every name the record leads to through include: and redirect= (the caller
    follows them), so a service named one level down counts."""
    found = _starting(records, "v=spf1")
    if not found:
        return Result(Check.missing, None, f"Add an SPF record (TXT) to {domain}.")
    if len(found) > 1:
        return Result(Check.wrong, found[0][:RECORD_SHOWN],
                      f"{domain} has {len(found)} SPF records; keep one.")  # fmt: skip
    record = found[0]
    if include and include.lower() not in reached | set(spf_links(record)):
        return Result(Check.wrong, record[:RECORD_SHOWN], f"Add include:{include} to it.")
    return Result(Check.ok, record[:RECORD_SHOWN], "SPF is set.")


def dkim(domain: str, selector: str | None, records: list[str]) -> Result:
    if not selector:
        note = "Give the DKIM selector your mail service uses (e.g. s1) to check it."
        return Result(Check.not_checked, None, note)
    name = f"{selector}._domainkey.{domain}"
    for record in records:
        tags = dict(
            part.strip().split("=", 1) for part in record.split(";") if "=" in part
        )  # fmt: skip
        if tags.get("p", "").strip():
            return Result(Check.ok, record[:RECORD_SHOWN], f"DKIM key found at {name}.")
    if records:
        return Result(Check.wrong, records[0][:RECORD_SHOWN], f"{name} has no DKIM key (p=).")
    return Result(Check.missing, None, f"No DKIM key at {name}.")


def dmarc(domain: str, records: list[str]) -> Result:
    found = _starting(records, "v=dmarc1")
    if not found:
        note = f"Add a DMARC record (TXT) at _dmarc.{domain}, e.g. v=DMARC1; p=none"
        return Result(Check.missing, None, note)
    return Result(Check.ok, found[0][:RECORD_SHOWN], "DMARC is set.")
