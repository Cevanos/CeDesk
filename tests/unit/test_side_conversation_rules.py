"""Side conversation rules (md X5), no database: who it can go to, the subject, the body."""

import pytest

from cedesk.helpdesk.domain import side_conversations as rules

OURS = {"support@acme.test", "billing@acme.test"}


def test_addresses_are_cleaned_once_each() -> None:
    got = rules.recipients([" Warehouse@Acme.com", "warehouse@acme.com", "courier@ship.in"],
                           customer="priya@example.com", ours=OURS)  # fmt: skip
    assert got == ["warehouse@acme.com", "courier@ship.in"]


@pytest.mark.parametrize(
    ("to", "why"),
    [
        (["Priya@Example.com"], "customer"),
        (["billing@acme.test"], "own addresses"),
        (["no-at-sign"], "isn't an email"),
        (["a@b.com\nBcc: x@y.com"], "isn't an email"),  # no header injection
        ([], "at least one"),
        ([f"p{n}@acme.com" for n in range(11)], "At most"),
    ],
)
def test_who_it_cant_go_to(to: list[str], why: str) -> None:
    with pytest.raises(rules.SideError, match=why):
        rules.recipients(to, customer="priya@example.com", ours=OURS)


def test_the_subject_names_the_ticket() -> None:
    assert (
        rules.subject(None, "Order   1234 missing", "T-1042") == "About Order 1234 missing [T-1042]"
    )
    assert rules.subject("  Courier check ", "x", "T-1") == "Courier check [T-1]"
    long = rules.subject("y" * 500, "x", "T-1042")
    assert len(long) <= rules.SUBJECT_MAX and long.endswith(" [T-1042]")
    assert rules.follow_up_subject("About x [T-1]") == "Re: About x [T-1]"
    assert rules.follow_up_subject("Re: About x [T-1]") == "Re: About x [T-1]"


def test_the_body() -> None:
    assert rules.body("  Shipped?  ") == "Shipped?"
    for bad in ("", "   ", "x" * (rules.BODY_MAX + 1)):
        with pytest.raises(rules.SideError):
            rules.body(bad)
