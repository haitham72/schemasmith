"""Shared application lifespan; original API assertions use this fixture unchanged."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def _database():
    with TestClient(app) as client:
        yield client
