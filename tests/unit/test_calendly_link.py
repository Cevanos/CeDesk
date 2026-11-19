"""The Calendly booking link (md PL7, W19), no database: a calendly.com page as the Admin
copies it, saved clean; anything else refused."""

import pytest

from cedesk.helpdesk.domain import apps as rules

CLEAN = "https://calendly.com/acme/30min"


@pytest.mark.parametrize(
    "copied",
    [
        CLEAN,
        f"  {CLEAN}/ ",
        # Live retest: the address bar, once the page loaded, was refused.
        f"{CLEAN}?month=2026-11",
        f"{CLEAN}?month=2026-11&date=2026-11-20",
        f"{CLEAN}#top",
        f"{CLEAN}?x=<b>",  # nothing from the query reaches /book
        "https://www.calendly.com/acme/30min",
        "calendly.com/acme/30min",
        "www.calendly.com/acme/30min?month=2026-11",
        "HTTPS://Calendly.com/acme/30min",
    ],
)
def test_a_copied_link_is_saved_clean(copied: str) -> None:
    assert rules.booking_link(copied) == CLEAN


def test_a_page_and_a_one_off_link() -> None:
    assert rules.booking_link("https://calendly.com/acme") == "https://calendly.com/acme"
    one_off = "https://calendly.com/d/ck7-2bv-3cq/30-minute-meeting"
    assert rules.booking_link(one_off + "?month=2026-11") == one_off


@pytest.mark.parametrize(
    "link",
    [
        "",
        "   ",
        "http://calendly.com/acme",
        "https://calendly.com.evil.test/acme",
        "https://evil.test/calendly.com/acme",
        "https://calendly.com/",
        "https://calendly.com/a/b/c",
        "https://calendly.com/d/a/b/c",
        "https://user@calendly.com/acme",
        "https://calendly.com:8443/acme",
        "https://calendly.com:443/acme",
        "https://calendly.com/a b",
        "https://calendly.com/acme/<b>",
        "javascript:alert(1)",
        "mailto:acme@calendly.com",
        "ftp://calendly.com/acme",
        "https://calendly.com/" + "a" * 300,
    ],
)
def test_anything_else_is_refused(link: str) -> None:
    with pytest.raises(rules.BadCredentials, match="Copy link"):
        rules.booking_link(link)
