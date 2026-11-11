"""Reading a forwarding-check code and DNS records for a support address (md C10)."""

from cedesk.helpdesk.domain.email_verify import (
    CODE_SUBJECT,
    Check,
    code_hash,
    code_in,
    dkim,
    dmarc,
    domain_of,
    new_code,
    spf,
    spf_links,
)


def test_the_code_survives_a_forwarder_touching_the_subject() -> None:
    code = new_code()
    subject = CODE_SUBJECT.format(code=code)
    for seen in (subject, f"Fwd: {subject}", f"[support] FW: {subject} (via Gmail)"):
        assert code_in(seen) == code
    assert code_in("CeDesk forwarding check short") is None
    assert code_in("") is None
    assert code_hash(code) != code and len(code_hash(code)) == 64


def test_domain() -> None:
    assert domain_of("Support@Acme.IN") == "acme.in"


def test_spf() -> None:
    assert spf("acme.in", [], None).status is Check.missing
    found = spf("acme.in", ["google-site-verification=x", "v=spf1 include:amazonses.com ~all"],
                "amazonses.com")  # fmt: skip
    assert (found.status, found.record) == (Check.ok, "v=spf1 include:amazonses.com ~all")
    other = spf("acme.in", ["v=spf1 include:_spf.google.com ~all"], "amazonses.com")
    assert (other.status, other.note) == (Check.wrong, "Add include:amazonses.com to it.")
    assert spf("acme.in", ["v=spf1 -all"], None).status is Check.ok  # no service named
    two = spf("acme.in", ["v=spf1 a ~all", "V=SPF1 mx ~all"], None)
    assert (two.status, two.note) == (Check.wrong, "acme.in has 2 SPF records; keep one.")


def test_dkim() -> None:
    assert dkim("acme.in", None, []).status is Check.not_checked
    assert dkim("acme.in", "s1", []).status is Check.missing
    key = "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEB"
    assert dkim("acme.in", "s1", [key]).status is Check.ok
    revoked = dkim("acme.in", "s1", ["v=DKIM1; p="])
    assert (revoked.status, revoked.note) == (
        Check.wrong,
        "s1._domainkey.acme.in has no DKIM key (p=).",
    )


def test_dmarc() -> None:
    assert dmarc("acme.in", ["v=DMARC1; p=none"]).status is Check.ok
    missing = dmarc("acme.in", ["something else"])
    assert missing.status is Check.missing and "_dmarc.acme.in" in missing.note


def test_spf_followed_through_includes_and_redirects() -> None:
    record = "v=spf1 a mx include:_spf.acme.in ~include:spf.protection.outlook.com " \
             "redirect=_spf.google.com include:%{i}.macro.test -all"  # fmt: skip
    assert spf_links(record) == ["_spf.acme.in", "spf.protection.outlook.com", "_spf.google.com"]
    nested = spf("acme.in", ["v=spf1 include:_spf.acme.in ~all"], "amazonses.com",
                 frozenset({"_spf.acme.in", "amazonses.com"}))  # fmt: skip
    assert nested.status is Check.ok
