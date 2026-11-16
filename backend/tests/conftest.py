import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("ml") / "models"


@pytest.fixture
def app_env(tmp_path, monkeypatch, model_dir):
    """Isolated settings: temp DB, uploads and model files; no OCR/embeddings downloads."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("MATCHER_BACKEND", "lexical")
    monkeypatch.setenv("OCR_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from app import db

    db.configure(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    yield db
    db.engine.dispose()


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def anon_client(app_env):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(anon_client):
    """Signed in as admin (all permissions)."""
    anon_client.headers.update(_login(anon_client, "admin", "admin123"))
    return anon_client


@pytest.fixture
def login():
    return _login
