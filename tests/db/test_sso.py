"""SAML single sign-on (md S1) through the real API with RLS on, against a fake identity
provider that signs its answers with a test key (xmlsec, as a real IdP would)."""

import base64
import zlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from onelogin.saml2.constants import OneLogin_Saml2_Constants as C
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from sqlalchemy import text

from cedesk.db import workspace_session
from cedesk.main import create_app
from cedesk.settings import Settings
from tests.db.helpers import PASSWORD, Sm, add, bearer, new_workspace, ok, sign_in

pytestmark = pytest.mark.db


@pytest.fixture
async def client(app_sessionmaker: Sm) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(sign_in_limit=100_000, api_url=API))
    app.state.sessionmaker = app_sessionmaker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


IDP = "https://idp.example.com/saml"
API = "https://cedesk.example.com"  # CEDESK_API_URL: what the IdP knows us by
SETTINGS = "/api/v1/sso-settings"


def key_and_cert() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test IdP")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = key_and_cert()
OTHER_KEY, OTHER_CERT = key_and_cert()


def at(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def answer(
    *,
    slug: str,
    request_id: str,
    email: str,
    key: str = KEY,
    cert: str = CERT,
    audience: str | None = None,
    until: timedelta = timedelta(minutes=5),
    signed: bool = True,
) -> str:
    """A SAML Response as an IdP posts it (base64), signed over the whole message."""
    acs = f"{API}/api/v1/sso/{slug}/acs"
    audience = audience or f"{API}/api/v1/sso/{slug}/metadata"
    xml = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
 xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r{uuid4().hex}" Version="2.0"
 IssueInstant="{at()}" Destination="{acs}" InResponseTo="{request_id}">
<saml:Issuer>{IDP}</saml:Issuer>
<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
<saml:Assertion ID="_a{uuid4().hex}" Version="2.0" IssueInstant="{at()}">
<saml:Issuer>{IDP}</saml:Issuer>
<saml:Subject>
<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>
<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
<saml:SubjectConfirmationData NotOnOrAfter="{at(until)}" Recipient="{acs}"
 InResponseTo="{request_id}"/>
</saml:SubjectConfirmation>
</saml:Subject>
<saml:Conditions NotBefore="{at(-timedelta(minutes=1))}" NotOnOrAfter="{at(until)}">
<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
</saml:Conditions>
<saml:AuthnStatement AuthnInstant="{at()}" SessionIndex="_s1"><saml:AuthnContext>
<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password</saml:AuthnContextClassRef>
</saml:AuthnContext></saml:AuthnStatement>
</saml:Assertion>
</samlp:Response>"""
    if signed:
        xml = OneLogin_Saml2_Utils.add_sign(
            xml, key, cert, sign_algorithm=C.RSA_SHA256, digest_algorithm=C.SHA256
        ).decode()
    return base64.b64encode(xml.encode()).decode()


async def desk(client: AsyncClient, sm: Sm) -> dict[str, Any]:
    slug = await new_workspace(sm)
    token = await sign_in(client, slug, "admin@example.com")
    neha = await add(client, token, "neha@example.com", "agent", "Neha")
    me = ok(await client.get("/api/v1/me", headers=bearer(token)))
    return {"slug": slug, "h": bearer(token), "neha": neha, "ws": UUID(me["workspace"]["id"])}


async def turn_on(client: AsyncClient, w: dict[str, Any], **extra: Any) -> Any:
    body = {
        "enabled": True,
        "idp_entity_id": IDP,
        "idp_sso_url": f"{IDP}/login",
        "idp_cert": CERT,
    } | extra
    return ok(await client.put(SETTINGS, headers=w["h"], json=body))


async def start(client: AsyncClient, w: dict[str, Any], next_path: str = "/tickets") -> str:
    """Starts a sign-in; returns the request id the IdP will answer. The browser cookie
    goes in `w["browser"]` (it is Secure, so the http test client wouldn't send it)."""
    r = await client.get(f"/api/v1/sso/{w['slug']}/start", params={"next": next_path})
    assert r.status_code == 302
    cookie = r.headers["set-cookie"]
    assert all(p in cookie for p in ("HttpOnly", "Secure", "SameSite=none", "Max-Age=600"))
    assert f"Path=/api/v1/sso/{w['slug']}/acs" in cookie
    w["browser"] = cookie.split(";", 1)[0].split("=", 1)[1]
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.headers["location"].startswith(f"{IDP}/login?")
    assert query["RelayState"] == [next_path]
    request_xml = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15).decode()
    return request_xml.split(' ID="', 1)[1].split('"', 1)[0]


async def post(
    client: AsyncClient, w: dict[str, Any], saml_response: str, browser: str | None = None
) -> dict[str, str]:
    r = await client.post(
        f"/api/v1/sso/{w['slug']}/acs",
        data={"SAMLResponse": saml_response, "RelayState": "/tickets"},
        headers={"cookie": f"cedesk_sso={browser or w['browser']}"},
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("http://localhost:3000/sso/callback#")
    return {k: v[0] for k, v in parse_qs(location.split("#", 1)[1]).items()}


async def test_settings_are_checked_and_admin_only(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    empty = ok(await client.get(SETTINGS, headers=w["h"]))
    assert (empty["enabled"], empty["acs_url"]) == (False, f"{API}/api/v1/sso/{w['slug']}/acs")
    good = {"enabled": True, "idp_entity_id": IDP, "idp_sso_url": f"{IDP}/login", "idp_cert": CERT}
    for bad in (
        {"enforced": True},
        {"enabled": True},
        good | {"idp_sso_url": "http://idp/x"},
        good | {"idp_cert": "not a cert"},
    ):
        assert (await client.put(SETTINGS, headers=w["h"], json=bad)).status_code == 422, bad
    saved = await turn_on(client, w)
    assert (saved["enabled"], saved["idp_entity_id"]) == (True, IDP)
    neha = bearer(await sign_in(client, w["slug"], "neha@example.com"))
    assert (await client.get(SETTINGS, headers=neha)).status_code == 403
    assert (await client.put(SETTINGS, headers=neha, json=good)).status_code == 403

    meta = await client.get(f"/api/v1/sso/{w['slug']}/metadata")
    assert f'entityID="{API}/api/v1/sso/{w["slug"]}/metadata"' in meta.text
    assert f'Location="{API}/api/v1/sso/{w["slug"]}/acs"' in meta.text
    status = ok(await client.get(f"/api/v1/sso/{w['slug']}"))
    assert (status["enabled"], status["start_url"]) == (
        True,
        f"{API}/api/v1/sso/{w['slug']}/start",
    )
    assert ok(await client.get("/api/v1/sso/no-such-place"))["enabled"] is False


async def test_signing_in_through_the_identity_provider(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await turn_on(client, w)
    request_id = await start(client, w)
    signed = answer(slug=w["slug"], request_id=request_id, email="NEHA@example.com")
    got = await post(client, w, signed)
    assert got["next"] == "/tickets" and "error" not in got
    me = ok(await client.get("/api/v1/me", headers=bearer(got["token"])))
    assert me["member"]["email"] == "neha@example.com"
    # The same answer again: refused (no replay).
    again = await post(client, w, signed)
    assert again["error"] == "This sign-in expired or was already used. Start again."

    # The IdP's MFA counts: requiring 2FA doesn't stop an SSO session.
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        await s.execute(
            text("INSERT INTO hd_security_settings (workspace_id, require_2fa) VALUES (:ws, true)"),
            {"ws": w["ws"]},
        )
    ok(await client.get("/api/v1/tickets", headers=bearer(got["token"])))


@pytest.mark.parametrize(
    "case",
    [
        "unsigned",
        "other key",
        "other audience",
        "expired",
        "not ours",
        "unknown member",
        "other browser",
    ],
)
async def test_bad_answers_are_refused(
    client: AsyncClient, app_sessionmaker: Sm, case: str
) -> None:
    w = await desk(client, app_sessionmaker)
    await turn_on(client, w)
    request_id = await start(client, w)
    args: dict[str, Any] = {
        "slug": w["slug"],
        "request_id": request_id,
        "email": "neha@example.com",
    }
    args |= {
        "unsigned": {"signed": False},
        "other key": {"key": OTHER_KEY, "cert": OTHER_CERT},
        "other audience": {"audience": "https://elsewhere.example.com"},
        "expired": {"until": timedelta(minutes=-1)},
        "not ours": {"request_id": "ONELOGIN_" + uuid4().hex},
        "unknown member": {"email": "stranger@example.com"},
        "other browser": {},
    }[case]
    # "other browser": a valid answer posted from a browser that didn't start the sign-in
    # (an attacker's own sign-in pushed into the victim's browser).
    browser = "not-the-starting-browser" if case == "other browser" else None
    got = await post(client, w, answer(**args), browser)
    assert "token" not in got and got["error"], got
    if case == "unknown member":
        assert got["error"].startswith("stranger@example.com isn't an active member")


async def test_enforced_sso_turns_password_sign_in_off_except_for_admins(
    client: AsyncClient, app_sessionmaker: Sm
) -> None:
    w = await desk(client, app_sessionmaker)
    await turn_on(client, w, enforced=True)
    r = await client.post(
        "/api/v1/auth/sign-in",
        json={"workspace": w["slug"], "email": "neha@example.com", "password": PASSWORD},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "SsoRequired")
    wrong = await client.post(
        "/api/v1/auth/sign-in",
        json={"workspace": w["slug"], "email": "neha@example.com", "password": "wrong password!"},
    )
    assert wrong.status_code == 401  # a wrong password learns nothing about SSO
    await sign_in(client, w["slug"], "admin@example.com")
    assert ok(await client.get(f"/api/v1/sso/{w['slug']}"))["enforced"] is True

    score = ok(await client.get("/api/v1/security/score", headers=w["h"]))
    assert next(i for i in score["items"] if i["key"] == "sso")["on"] is True
    async with workspace_session(app_sessionmaker, w["ws"]) as s:
        whats = (
            (
                await s.execute(
                    text(
                        "SELECT payload->>'what' FROM hd_outbox WHERE event = 'security.alert' "
                        "ORDER BY occurred_at"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert whats[-2:] == [
        "Single sign-on was turned on.",
        "Password sign-in is now off for everyone except Admins (SSO required).",
    ]
