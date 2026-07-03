import os
import pytest
import jwt
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, User
from backend.app import config

TEST_DB_PATH = "test_temp_auth.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    
    def test_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
            
    app.dependency_overrides[get_db] = test_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

@pytest.fixture(autouse=True)
def setup_keys(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_register_bootstrap_protection(client):
    """Garante que a criação de usuários exige o header X-Admin-Key correto."""
    response = client.post(
        "/auth/register",
        json={"username": "user1", "password": "password123"}
    )
    assert response.status_code == 401

    response_bad_key = client.post(
        "/auth/register",
        json={"username": "user1", "password": "password123"},
        headers={"X-Admin-Key": "wrong-key"}
    )
    assert response_bad_key.status_code == 401

def test_register_success(client):
    """Garante que o registro de usuário funciona e não vaza a senha nem o hash."""
    response = client.post(
        "/auth/register",
        json={"username": "new_admin", "password": "secretpassword123"},
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "new_admin"
    assert "id" in data
    assert "created_at" in data
    assert "password" not in data
    assert "hashed_password" not in data
    assert "secretpassword123" not in response.text

def test_register_duplicate_username(client):
    """Garante erro 409 ao registrar usuário duplicado."""
    payload = {"username": "duplicate_user", "password": "password123"}
    r1 = client.post(
        "/auth/register",
        json=payload,
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/auth/register",
        json=payload,
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert r2.status_code == 409
    assert "username" in r2.json()["detail"].lower()

def test_login_success(client):
    """Garante login com credenciais corretas gerando JWT válido."""
    # Primeiro registra
    client.post(
        "/auth/register",
        json={"username": "test_user", "password": "correct_password"},
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )

    # Depois tenta logar
    response = client.post(
        "/auth/login",
        data={"username": "test_user", "password": "correct_password"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_invalid_credentials(client):
    """Garante erro 401 com senha inválida ou usuário inexistente."""
    # Registra
    client.post(
        "/auth/register",
        json={"username": "test_user", "password": "correct_password"},
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )

    # Senha incorreta
    r1 = client.post(
        "/auth/login",
        data={"username": "test_user", "password": "wrong_password"}
    )
    assert r1.status_code == 401

    # Usuário incorreto
    r2 = client.post(
        "/auth/login",
        data={"username": "non_existent", "password": "correct_password"}
    )
    assert r2.status_code == 401

def test_get_current_user_profile(client):
    """Garante que a rota /auth/me protege o endpoint e retorna o perfil correto."""
    # Acesso sem token
    r1 = client.get("/auth/me")
    assert r1.status_code == 401

    # Registra e loga
    client.post(
        "/auth/register",
        json={"username": "logged_user", "password": "password123"},
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    login_res = client.post(
        "/auth/login",
        data={"username": "logged_user", "password": "password123"}
    )
    token = login_res.json()["access_token"]

    # Acesso com token válido
    r2 = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["username"] == "logged_user"

def test_expired_token(client, monkeypatch):
    """Garante erro 401 com token expirado."""
    # Cria token expirado manualmente usando a chave de teste
    expire = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "test_user",
        "exp": expire
    }
    expired_token = jwt.encode(payload, "test-jwt-secret-key-123456789", algorithm="HS256")

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401
    assert "expirado" in response.json()["detail"].lower()

def test_malformed_authorization_header(client):
    """Garante erro 401 com Authorization malformado."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": "BearerMalformed token123"}
    )
    assert response.status_code == 401

def test_regression_unauthorized_endpoints(client):
    """Garante que endpoints anteriormente abertos agora exigem autenticação JWT."""
    # 1. POST /products
    r1 = client.post(
        "/products",
        json={"title": "Test", "marketplace": "mercado_livre", "price": 10.0}
    )
    assert r1.status_code == 401

    # 2. POST /suggestions/1/approve
    r2 = client.post(
        "/suggestions/1/approve"
    )
    assert r2.status_code == 401

    # 3. POST /imports/1/confirm
    r3 = client.post(
        "/imports/1/confirm"
    )
    assert r3.status_code == 401


