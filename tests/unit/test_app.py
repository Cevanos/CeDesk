from fastapi.testclient import TestClient

from cedesk.main import create_app
from cedesk.settings import Settings


def test_healthz_answers_without_a_database() -> None:
    # An unreachable URL: /healthz must not touch the database.
    app = create_app(Settings(database_url="postgresql+asyncpg://x:y@127.0.0.1:1/none"))
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_says_unavailable_when_the_database_is_down() -> None:
    app = create_app(Settings(database_url="postgresql+asyncpg://someone:hunter2@127.0.0.1:1/none"))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "hunter2" not in response.text


def test_openapi_lists_the_health_routes() -> None:
    app = create_app(Settings())
    paths = app.openapi()["paths"]
    assert "/healthz" in paths
    assert "/readyz" in paths


def _client() -> TestClient:
    return TestClient(
        create_app(Settings(database_url="postgresql+asyncpg://x:y@127.0.0.1:1/none"))
    )


def test_validation_errors_use_the_error_envelope_with_field_details() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/auth/sign-in",
            json={"workspace": "acme", "email": "not-an-email", "password": "s3cret-pw", "x": 1},
        )
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "Invalid"
    assert error["message"] == "Some fields are not valid."
    assert {d["field"] for d in error["details"]} == {"email", "x"}
    assert all(d["message"] for d in error["details"])
    # The submitted values are never echoed back.
    assert "s3cret-pw" not in r.text
    assert "not-an-email" not in r.text


def test_other_errors_use_the_same_envelope() -> None:
    with _client() as client:
        missing = client.get("/api/v1/nowhere")
        wrong_method = client.delete("/healthz")
        unsigned = client.get("/api/v1/me")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "NotFound", "message": "Not Found"}}
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "MethodNotAllowed"
    assert unsigned.status_code == 401
    assert unsigned.json() == {"error": {"code": "NotSignedIn", "message": "Sign in first."}}
    assert unsigned.headers["www-authenticate"] == "Bearer"
