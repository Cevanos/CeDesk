"""Portal rules that need no database (md P1, C2)."""

import pytest

from cedesk.helpdesk.domain.portal import clean_name, contact_name


@pytest.mark.parametrize(
    ("email", "name"),
    [
        ("priya.shah@example.org", "Priya Shah"),
        ("pranav.katariya@example.com", "Pranav Katariya"),
        ("ravi_k+orders@example.org", "Ravi K"),  # the +tag is not part of the name
        ("j-smith99@example.org", "J Smith99"),
        ("info@acme.com", "Info"),
        ("anna.2024@example.org", "Anna"),  # a part with no letters is left out
        ("12345@example.org", "12345"),  # no letters at all: as it is
        ("राहुल.शर्मा@example.in", "राहुल शर्मा"),
    ],
)
def test_a_new_customer_is_named_from_their_email(email: str, name: str) -> None:
    """Live: a portal reply said "Hi pranav.katariya"; {{customer.first_name}} is the
    first word of this name, so it now says "Hi Pranav"."""
    assert contact_name(email) == name


def test_the_web_form_keeps_a_typed_name() -> None:
    assert clean_name("  Ann   Lee ", "a@b.co") == "Ann Lee"
    assert clean_name("", "priya.shah@example.org") == "Priya Shah"
