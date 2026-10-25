import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from citypulse.main import create_app
from citypulse.settings import Settings

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def project_version() -> str:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_ok_and_the_package_version() -> None:
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": project_version()}


def test_openapi_docs_are_served() -> None:
    with client() as c:
        docs = c.get("/docs")
        schema = c.get("/openapi.json").json()
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    assert "/health" in schema["paths"]
    assert schema["info"]["version"] == project_version()


def test_unknown_path_is_404() -> None:
    assert client().get("/nope").status_code == 404


def test_create_app_keeps_the_given_settings() -> None:
    settings = Settings(database_url="postgresql://user@localhost/test")
    assert create_app(settings).state.settings is settings
