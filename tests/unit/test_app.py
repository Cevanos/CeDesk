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


def test_openapi_lists_the_health_routes() -> None:
    app = create_app(Settings())
    paths = app.openapi()["paths"]
    assert "/healthz" in paths
    assert "/readyz" in paths
