import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app import config

class AuthTestClient(TestClient):
    def __init__(self, *args, **kwargs):
        self.auth_headers = kwargs.pop("auth_headers", {})
        super().__init__(*args, **kwargs)

    def request(self, method, url, **kwargs):
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
        else:
            headers = dict(headers)
            
        if "skip_auth" in headers:
            headers.pop("skip_auth")
            headers.pop("Authorization", None)
            headers.pop("X-Admin-Key", None)
        elif "Authorization" not in headers:
            headers.update(self.auth_headers)
            
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)

@pytest.fixture
def client(monkeypatch):
    """Fixture client que fornece um AuthTestClient com autenticação JWT ativa por padrão."""
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    
    with TestClient(app) as base_client:
        # Cria o usuário admin de teste
        base_client.post(
            "/auth/register",
            json={
                "username": "test_admin_user",
                "password": "securepassword123"
            },
            headers={"X-Admin-Key": "test-admin-secret-key"}
        )
        
        # Login
        login_res = base_client.post(
            "/auth/login",
            data={
                "username": "test_admin_user",
                "password": "securepassword123"
            }
        )
        token = login_res.json()["access_token"]
        
    auth_client = AuthTestClient(app, auth_headers={"Authorization": f"Bearer {token}"})
    yield auth_client

@pytest.fixture
def auth_headers(monkeypatch):
    """Fixture que retorna os cabeçalhos de autorização JWT prontos."""
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    
    with TestClient(app) as base_client:
        base_client.post(
            "/auth/register",
            json={
                "username": "test_admin_user",
                "password": "securepassword123"
            },
            headers={"X-Admin-Key": "test-admin-secret-key"}
        )
        login_res = base_client.post(
            "/auth/login",
            data={
                "username": "test_admin_user",
                "password": "securepassword123"
            }
        )
        token = login_res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}
