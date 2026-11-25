"""Campaign rules with no database (feature-gap plan phase 6): the suppression hash,
who may be mailed, the content (placeholders escaped in the HTML, no raw HTML, safe
links), the footer and unsubscribe headers, caps, the bounce pause, schedules, and
reading bounces and spam reports."""

import hashlib
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from uuid import UUID

import pytest

from cedesk.helpdesk.domain import bounces
from cedesk.helpdesk.domain import campaigns as rules
from cedesk.helpdesk.domain.campaigns import Limits, Links
from cedesk.mail import OutgoingEmail, build

WS = UUID("8b0e6c1e-0000-4000-8000-000000000001")
FOOTER = rules.Footer("Acme", "Acme Ltd\n12 MG Road", "https://p.test/unsubscribe#token=t")


def test_the_suppression_hash_is_per_workspace_and_ignores_case() -> None:
    h = rules.email_hash(WS, " Ana@Shop.Test ")
    assert h == hashlib.sha256(f"{WS}:ana@shop.test".encode()).digest()
    assert h != rules.email_hash(UUID(int=2), "ana@shop.test")


@pytest.mark.parametrize(
    ("email", "ok"),
    [("ana@shop.test", True), ("chat-1@cevanos.invalid", False), ("postmaster@x.test", False),
     ("no-reply@x.test", False), ("nope", False)],
)  # fmt: skip
def test_who_may_be_mailed(email: str, ok: bool) -> None:
    assert rules.mailable(email) is ok


def test_placeholders_are_filled_and_escaped_in_the_html() -> None:
    out = rules.render(
        subject="Hi {{customer.first_name}}",
        body="Dear {{customer.name}},\n\nYour plan: {{fields.plan}}. From {{agent.name}}",
        customer_name="[Win](https://evil.test) <b>",
        sender_name="Acme News",
        fields={"plan": "**gold**"},
        footer=FOOTER,
    )
    assert out.subject == "Hi [Win](https://evil.test)"
    assert "evil.test" not in out.html.split("<hr")[0].replace("[Win](https://evil.test)", "")
    assert "<b>" not in out.html and "&lt;b&gt;" in out.html
    assert "<strong>" not in out.html  # a field's ** stays text
    assert "Dear [Win](https://evil.test) <b>," in out.text  # the text part is as given


def test_markdown_subset_and_no_raw_html() -> None:
    html = rules.to_html(
        "# Big news\n\n- one\n- **two**\n\n1. first\n2. second\n\n"
        "Line one\nline two <script>x</script> [ok](https://a.test/x?y=1&z=2) "
        "[bad](javascript:alert(1)) https://b.test/p"
    )
    assert "<h1>Big news</h1>" in html
    assert "<ul><li>one</li><li><strong>two</strong></li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert "Line one<br>" in html and "&lt;script&gt;" in html and "<script>" not in html
    assert '<a href="https://a.test/x?y=1&amp;z=2">ok</a>' in html
    assert "javascript:" not in html.replace("[bad](javascript", "")
    assert '<a href="https://b.test/p">https://b.test/p</a>' in html
    assert rules.to_text("see [it](https://a.test)") == "see it (https://a.test)"


def test_footer_pixel_and_headers() -> None:
    links = Links("https://portal.test/", "https://api.test")
    assert links.unsubscribe_page("T") == "https://portal.test/unsubscribe#token=T"
    assert links.one_click("T") == "https://api.test/api/v1/public/unsubscribe/T"
    assert links.problem(require_https=True) is None
    assert Links("http://x", "http://x").problem(require_https=True) is not None
    assert Links("http://x", "http://x").problem(require_https=False) is None
    f = rules.Footer("A & B", "1 Road\nCity", "https://u.test/x", "https://api.test/open/P")
    html = rules.footer_html(f)
    assert (
        "A &amp; B" in html and "1 Road<br>City" in html and 'src="https://api.test/open/P"' in html
    )
    assert "Unsubscribe: https://u.test/x" in rules.footer_text(f)
    headers = dict(rules.unsubscribe_headers("https://api.test/u/T"))
    assert headers["List-Unsubscribe"] == "<https://api.test/u/T>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_an_email_with_html_and_headers() -> None:
    email = OutgoingEmail(
        from_address="news@acme.test", from_name="Acme", to=["ana@shop.test"], subject="Hi",
        text="Plain", html="<p>Rich</p>", headers=(("List-Unsubscribe", "<https://u.test>"),),
    )  # fmt: skip
    msg = message_from_bytes(build(email).as_bytes(), policy=policy.default)
    assert msg["List-Unsubscribe"] == "<https://u.test>"
    assert msg.get_content_type() == "multipart/alternative"
    kinds = [p.get_content_type() for p in msg.iter_parts()]  # type: ignore[attr-defined]
    assert kinds == ["text/plain", "text/html"]
    plain = OutgoingEmail(from_address="a@b.test", from_name="A", to=["c@d.test"],
                          subject="s", text="t")  # fmt: skip
    assert build(plain).get_content_type() == "text/plain"


def test_caps_pause_and_schedule() -> None:
    limits = Limits(max_recipients=10, daily_cap=20, per_minute=5)
    assert rules.cap_problem(0, 0, limits) is not None
    assert rules.cap_problem(10, 10, limits) is None
    assert "the most is 10" in (rules.cap_problem(11, 0, limits) or "")
    assert "daily limit" in (rules.cap_problem(5, 16, limits) or "")


def test_nobody_to_send_to_says_why() -> None:
    limits = Limits(max_recipients=10, daily_cap=20, per_minute=5)
    why = rules.cap_problem(0, 0, limits, in_segment=0)
    assert why is not None and "no customers" in why
    why = rules.cap_problem(0, 0, limits, in_segment=2, not_mailable=2)
    assert why is not None and "2 can't be emailed" in why and "suppressed" not in why
    why = rules.cap_problem(0, 0, limits, in_segment=5, not_mailable=1, suppressed=4)
    assert why is not None and "1 can't be emailed" in why and "4 are suppressed" in why
    assert rules.cap_problem(3, 0, limits, in_segment=5, not_mailable=1, suppressed=1) is None
    assert not rules.should_pause(40, 10)  # too few sent to judge
    assert not rules.should_pause(100, 5) and rules.should_pause(100, 6)
    assert rules.retry_wait(1) == timedelta(minutes=5) and rules.retry_wait(3) is None
    now = datetime.now(UTC)
    assert rules.check_schedule(None, now) is None
    assert rules.check_schedule(now - timedelta(minutes=1), now) is None
    later = now + timedelta(days=1)
    assert rules.check_schedule(later, now) == later
    with pytest.raises(rules.CampaignError):
        rules.check_schedule(now + timedelta(days=91), now)
    assert rules.dns_ok({"spf": {"status": "ok"}, "dkim": {"status": "ok"}})
    assert not rules.dns_ok({"spf": {"status": "ok"}, "dkim": {"status": "missing"}})
    assert not rules.dns_ok(None)


def test_the_checks_before_a_send() -> None:
    ready = dict(subject="s", body="b", segment=True, sender=True, sender_verified=True,
                 dns_ok=True, postal_address=True)  # fmt: skip
    assert rules.ready_problems(**ready) == []
    assert len(rules.ready_problems(**(ready | {"subject": "", "postal_address": False}))) == 2
    assert "Verify" in rules.ready_problems(**(ready | {"sender_verified": False}))[0]


def test_cleaning_and_audit_text() -> None:
    assert rules.clean_postal("  Acme Ltd \n\n 12 Road, City  ") == "Acme Ltd\n12 Road, City"
    with pytest.raises(rules.CampaignError):
        rules.clean_postal("short")
    with pytest.raises(rules.CampaignError):
        rules.clean_name("  ")
    assert rules.without_addresses("from ana@shop.test today") == "from (address) today"


def _dsn(status: str, action: str = "failed") -> bytes:
    return (
        "From: MAILER-DAEMON@mx.test\r\nTo: news@acme.test\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nFailed.\r\n"
        "--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.test\r\n\r\n"
        f"Final-Recipient: rfc822; Ana@Shop.test\r\nAction: {action}\r\nStatus: {status}\r\n\r\n"
        "--B\r\nContent-Type: message/rfc822\r\n\r\n"
        "From: news@acme.test\r\nMessage-ID: <abc@acme.test>\r\nSubject: Hi\r\n\r\nBody\r\n"
        "--B--\r\n"
    ).encode()


def test_reading_bounces() -> None:
    hard = bounces.read(_dsn("5.1.1"))
    assert hard == bounces.Report(bounces.Kind.hard, ("ana@shop.test",), ("<abc@acme.test>",))
    soft = bounces.read(_dsn("4.2.2"))
    assert soft is not None and soft.kind is bounces.Kind.soft
    delayed = bounces.read(_dsn("4.4.7", action="delayed"))
    assert delayed is not None and delayed.kind is bounces.Kind.soft
    assert bounces.read(_dsn("2.0.0", action="delivered")) is None
    assert bounces.read(b"From: a@b.test\r\nSubject: Out of office\r\n\r\nAway") is None
    assert bounces.read(b"\x00\x01 not an email") is None


def test_reading_a_spam_report() -> None:
    raw = (
        b"From: fbl@isp.test\r\nTo: news@acme.test\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/report; report-type=feedback-report; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nSpam.\r\n"
        b"--B\r\nContent-Type: message/feedback-report\r\n\r\nFeedback-Type: abuse\r\n"
        b"Original-Rcpt-To: cy@shop.test\r\n\r\n"
        b"--B\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: <x1@acme.test>\r\n\r\n"
        b"--B--\r\n"
    )
    found = bounces.read(raw)
    assert found == bounces.Report(bounces.Kind.complaint, ("cy@shop.test",), ("<x1@acme.test>",))
