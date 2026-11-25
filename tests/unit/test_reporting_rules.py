"""Reporting logins' pure rules (differentiator plan #7): names, passwords, and the SCRAM
verifier sent instead of the password."""

import base64
import hashlib
import hmac

import pytest

from cedesk.helpdesk.domain import reporting as r


def test_names_are_report_then_lowercase() -> None:
    assert r.check_name("report_bi") == "report_bi"
    assert r.check_name("report_metabase_2") == "report_metabase_2"
    for bad in ("bi", "report_", "report_B", "report_a-b", "report_x;drop", "cedesk_app",
                "report_" + "a" * 41):  # fmt: skip
        with pytest.raises(r.ReportingError):
            r.check_name(bad)


def test_passwords_are_16_or_more() -> None:
    assert r.check_password("x" * 16)
    for bad in ("x" * 15, "x" * 20 + "\x00"):
        with pytest.raises(r.ReportingError):
            r.check_password(bad)


def test_the_verifier_is_postgresqls_scram_format_and_never_the_password() -> None:
    salt = b"0123456789abcdef"
    found = r.scram_verifier("correct horse battery", salt=salt)
    head, keys = found.split("$", 1)[1].split("$")
    iterations, salt_b64 = head.split(":")
    stored_b64, server_b64 = keys.split(":")
    assert found.startswith("SCRAM-SHA-256$4096:") and "correct horse" not in found
    assert base64.b64decode(salt_b64) == salt and int(iterations) == r.SCRAM_ITERATIONS
    salted = hashlib.pbkdf2_hmac("sha256", b"correct horse battery", salt, 4096)
    client = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    assert base64.b64decode(stored_b64) == hashlib.sha256(client).digest()
    assert base64.b64decode(server_b64) == hmac.new(salted, b"Server Key",
                                                    hashlib.sha256).digest()  # fmt: skip
    # A fresh salt each time.
    assert r.scram_verifier("same") != r.scram_verifier("same")
