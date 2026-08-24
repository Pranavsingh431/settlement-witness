"""Tests for the health endpoint."""

from fastapi.testclient import TestClient

from app import __version__


def test_health_reports_version_and_environment(client: TestClient) -> None:
    """The health endpoint returns the running version and the active environment."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "environment": "test",
    }


def test_openapi_schema_is_served(client: TestClient) -> None:
    """The application publishes a schema, which proves the app factory wired up."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
