"""SAML 2.0 service provider for single sign-on (md S1), on python3-saml (xmlsec).

CeDesk sends an AuthnRequest (HTTP-Redirect) and takes the identity provider's answer at
its ACS URL (HTTP-POST). An answer is accepted only when it is signed with the IdP's
certificate (the whole response or the assertion; unsigned answers are always refused),
is meant for us (audience, destination), is in its time window, uses no SHA-1, and
answers a request we made (`request_id`). Requests are not signed, which every common
IdP (Okta, Microsoft Entra ID, Google, OneLogin, JumpCloud) accepts.
"""

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request
from onelogin.saml2.errors import OneLogin_Saml2_Error
from onelogin.saml2.response import OneLogin_Saml2_Response
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.utils import OneLogin_Saml2_Utils

# Attributes IdPs use for the email, tried before the NameID.
EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)


class SamlError(ValueError):
    """The answer can't be accepted; the message says why (for the Admin, not secret)."""


@dataclass(frozen=True)
class Idp:
    entity_id: str
    sso_url: str
    cert: str  # PEM, or its base64 body


@dataclass(frozen=True)
class Sp:
    entity_id: str
    acs_url: str


def cert_body(pem: str) -> str:
    """The certificate as one base64 line; raises SamlError when it isn't one."""
    body = "".join(
        line.strip() for line in pem.strip().splitlines() if line.strip() and "-----" not in line
    )
    try:
        x509.load_der_x509_certificate(base64.b64decode(body, validate=True))
    except ValueError as exc:
        raise SamlError("That isn't an X.509 certificate (PEM).") from exc
    return body


def _settings(sp: Sp, idp: Idp | None) -> OneLogin_Saml2_Settings:
    config: dict[str, Any] = {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp.entity_id,
            "assertionConsumerService": {
                "url": sp.acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "security": {
            "authnRequestsSigned": False,
            "wantAssertionsSigned": False,  # the response or the assertion must be signed
            "wantMessagesSigned": False,
            "wantNameId": True,
            "requestedAuthnContext": False,  # let the IdP use its own MFA
            "rejectDeprecatedAlgorithm": True,
            "wantAttributeStatement": False,
        },
    }
    if idp is not None:
        config["idp"] = {
            "entityId": idp.entity_id,
            "singleSignOnService": {
                "url": idp.sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": cert_body(idp.cert),
        }
    try:
        return OneLogin_Saml2_Settings(config, sp_validation_only=idp is None)
    except OneLogin_Saml2_Error as exc:  # e.g. an address it can't use
        raise SamlError(f"Single sign-on can't be set up with these addresses: {exc}") from exc


def _request_data(acs_url: str, post: dict[str, str] | None = None) -> dict[str, Any]:
    """The request as python3-saml wants it, built from our own ACS URL: the answer's
    Destination must be exactly that, whatever proxies sit in front of the API."""
    url = urlsplit(acs_url)
    return {
        "https": "on" if url.scheme == "https" else "off",
        "http_host": url.netloc,  # with the port, when there is one
        "script_name": url.path,
        "get_data": {},
        "post_data": post or {},
    }


def login_url(sp: Sp, idp: Idp, relay_state: str) -> tuple[str, str]:
    """(where to send the browser, the request id to remember)."""
    request = OneLogin_Saml2_Authn_Request(_settings(sp, idp))
    url = OneLogin_Saml2_Utils.redirect(
        idp.sso_url, {"SAMLRequest": request.get_request(), "RelayState": relay_state}
    )
    return str(url), str(request.get_id())


def answered_request(sp: Sp, idp: Idp, saml_response: str) -> str | None:
    """The request id an answer says it answers, read before it is checked (to find our
    request); `read_answer` then checks it."""
    try:
        found = OneLogin_Saml2_Response(_settings(sp, idp), saml_response).get_in_response_to()
    except Exception:
        return None
    return str(found) if found else None


@dataclass(frozen=True)
class Identity:
    email: str
    assertion_id: str


def read_answer(sp: Sp, idp: Idp, saml_response: str, request_id: str) -> Identity:
    """Checks the answer fully and returns who signed in."""
    try:
        response = OneLogin_Saml2_Response(_settings(sp, idp), saml_response)
        response.is_valid(
            _request_data(sp.acs_url, {"SAMLResponse": saml_response}),
            request_id,
            raise_exceptions=True,
        )
    except SamlError:
        raise
    except Exception as exc:
        raise SamlError(f"The identity provider's answer was refused: {exc}") from exc
    attributes = {k.lower(): v for k, v in (response.get_attributes() or {}).items()}
    email = next(
        (str(attributes[a][0]) for a in EMAIL_ATTRIBUTES if attributes.get(a)),
        str(response.get_nameid() or ""),
    )
    if "@" not in email:
        raise SamlError("The identity provider didn't send an email address.")
    return Identity(email=email.strip().lower(), assertion_id=str(response.get_assertion_id()))


def metadata(sp: Sp) -> str:
    """Our service provider metadata, for the IdP's setup screen."""
    return str(_settings(sp, None).get_sp_metadata())
