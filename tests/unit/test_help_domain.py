"""Help center custom domain rules (feature-gap plan 4.2) that need no database."""

import pytest

from cedesk.helpdesk.domain import help_domain as h
from cedesk.settings import Settings


def test_a_domain_is_cleaned_to_lower_case() -> None:
    assert h.clean_domain(" Help.Example.COM ") == "help.example.com"
    assert h.clean_domain("help.example.com.") == "help.example.com"  # the root dot
    assert h.clean_domain("a-b.example.co.uk") == "a-b.example.co.uk"
    assert h.clean_domain("xn--bcher-kva.example") == "xn--bcher-kva.example"


def test_the_shortest_domain_matches_the_tables_check() -> None:
    assert h.clean_domain("a.bc") == "a.bc"  # 4 characters
    with pytest.raises(h.BadDomain):
        h.clean_domain("a.b")  # 3: the table would refuse it


def test_an_international_name_is_stored_in_its_ascii_form() -> None:
    assert h.clean_domain("bücher.example") == "xn--bcher-kva.example"
    assert h.clean_domain("सहायता.भारत").startswith("xn--")


@pytest.mark.parametrize(
    "bad",
    [
        "", "   ", "localhost", "example", "https://help.example.com", "help.example.com/path",
        "a.b", "help.example.com:8080", "*.example.com", "user@help.example.com",
        "help.example.com?x=1",
        "help .example.com", "help_desk.example.com", "-bad.example.com", "bad-.example.com",
        "a..example.com", ".example.com", "192.168.0.1", "10.0.0.1", "[::1]", "::1",
        "x" * 64 + ".example.com", ("a." * 130) + "com",
    ],
)  # fmt: skip
def test_things_that_are_not_a_plain_domain_are_refused(bad: str) -> None:
    with pytest.raises(h.BadDomain):
        h.clean_domain(bad)


def test_the_platforms_own_hosts_and_everything_under_them_are_reserved() -> None:
    reserved = h.hosts_of(
        ["https://app.cedesk.in", "", "http://localhost:3000", "//help.cedesk.in"]
    )
    assert reserved == {"app.cedesk.in", "localhost", "help.cedesk.in"}
    assert h.is_reserved("app.cedesk.in", reserved)
    assert h.is_reserved("x.app.cedesk.in", reserved)
    assert h.is_reserved("help.cedesk.in", reserved)
    assert not h.is_reserved("cedesk.in", reserved)  # the parent is not one of the hosts
    assert not h.is_reserved("myapp.cedesk.in", reserved)  # a suffix is not a child
    assert not h.is_reserved("help.example.com", reserved)
    assert not h.is_reserved("anything.com", [])


def test_hosts_of_skips_what_is_not_a_url() -> None:
    assert h.hosts_of(["https://[::1", "not a url", "https://EXAMPLE.com/path"]) == {"example.com"}


def test_the_proof_is_a_txt_record_holding_the_workspaces_token() -> None:
    token = h.new_token()
    assert len(token) >= 20 and token != h.new_token()
    assert h.txt_name("help.example.com") == "_cedesk-verify.help.example.com"
    assert h.txt_value(token) == f"cedesk-verify={token}"
    assert h.token_present([f"cedesk-verify={token}"], token)
    assert h.token_present(["v=spf1 -all", f' "cedesk-verify={token}" '], token)  # quotes, spaces
    assert not h.token_present([], token)
    assert not h.token_present([f"cedesk-verify={token}x"], token)
    assert not h.token_present(["cedesk-verify=" + h.new_token()], token)  # another workspace's
    assert not h.token_present([token], token)  # the bare token is not the record


def test_the_tls_key_must_be_long_enough_outside_dev() -> None:
    weak = Settings(env="staging", tls_ask_key="short")  # type: ignore[arg-type]
    assert any("TLS_ASK_KEY" in p for p in weak.unsafe())
    strong = Settings(env="staging", tls_ask_key="k" * 24)  # type: ignore[arg-type]
    assert not any("TLS_ASK_KEY" in p for p in strong.unsafe())
    assert not any("TLS_ASK_KEY" in p for p in Settings(env="staging").unsafe())  # off: fine
