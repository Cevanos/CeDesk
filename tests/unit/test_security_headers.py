"""Every API answer carries the browser protections; a route's own stricter header stays,
the interactive docs still load, and HSTS is sent only over https."""

from fastapi import Response
from fastapi.testclient import TestClient

from cedesk.main import create_app
from cedesk.settings import Settings

NO_DB = "postgresql+asyncpg://x:y@127.0.0.1:1/none"


def _client(base_url: str = "http://testserver") -> TestClient:
    return TestClient(create_app(Settings(database_url=NO_DB)), base_url=base_url)


def test_json_answers_and_errors_carry_the_headers() -> None:
    with _client() as client:
        for r in (client.get("/healthz"), client.get("/api/v1/me"), client.get("/nope")):
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["X-Frame-Options"] == "DENY"
            assert r.headers["Referrer-Policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
            assert "default-src 'none'" in r.headers["Content-Security-Policy"]
            assert "Strict-Transport-Security" not in r.headers  # plain http


def test_hsts_only_over_https() -> None:
    with _client("https://testserver") as client:
        r = client.get("/healthz")
    assert r.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_the_docs_page_keeps_working() -> None:
    with _client() as client:
        r = client.get("/docs")
    assert r.status_code == 200
    assert "Content-Security-Policy" not in r.headers  # it loads its own scripts
    assert r.headers["X-Frame-Options"] == "DENY"


def test_a_routes_own_csp_is_kept() -> None:
    """As file downloads do: their sandbox CSP is not replaced by the default."""
    app = create_app(Settings(database_url=NO_DB))

    @app.get("/test-own-csp")
    async def own(response: Response) -> dict[str, str]:
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return {}

    with TestClient(app) as client:
        assert client.get("/test-own-csp").headers["Content-Security-Policy"] == (
            "default-src 'none'; sandbox"
        )
