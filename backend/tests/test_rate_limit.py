import os
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, User, AuthAttempt
from backend.app import config

TEST_DB_PATH = "test_temp_rate.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    """Recria a estrutura de tabelas no banco de dados temporário e sobrescreve a dependência get_db."""
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

@pytest.fixture
def rate_client(monkeypatch):
    """Client de testes com rate limiting configurado com valores padrão baixos."""
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-rate-limit-123")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key-rate")
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_MINUTES", 15)
    
    with TestClient(app) as c:
        yield c

def test_login_lockout_by_username(rate_client):
    """Garante que LOGIN_MAX_ATTEMPTS tentativas falhas de login bloqueiam a conta por username."""
    db = TestingSessionLocal()
    # 1. Cria usuário de teste
    from backend.app.security.auth import hash_password
    user = User(username="rate_user_1", hashed_password=hash_password("correct_password"))
    db.add(user)
    db.commit()

    # 2. Faz 5 tentativas de login com senha errada
    for _ in range(5):
        res = rate_client.post(
            "/auth/login",
            data={"username": "rate_user_1", "password": "wrong_password"}
        )
        assert res.status_code == 401

    # 3. A 6ª tentativa com a senha CORRETA deve falhar com 429
    res = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_1", "password": "correct_password"}
    )
    assert res.status_code == 429
    assert "muitas tentativas" in res.json()["detail"].lower()

def test_login_user_isolation(rate_client):
    """Garante que o bloqueio do usuário A não afeta o usuário B."""
    db = TestingSessionLocal()
    from backend.app.security.auth import hash_password
    u1 = User(username="rate_user_a", hashed_password=hash_password("pass_a"))
    u2 = User(username="rate_user_b", hashed_password=hash_password("pass_b"))
    db.add_all([u1, u2])
    db.commit()

    # Bloqueia rate_user_a vindo de um IP
    for _ in range(5):
        rate_client.post(
            "/auth/login",
            data={"username": "rate_user_a", "password": "wrong_password"},
            headers={"X-Forwarded-For": "198.51.100.10"}
        )

    # Verifica que rate_user_a está bloqueado
    res_a = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_a", "password": "pass_a"},
        headers={"X-Forwarded-For": "198.51.100.10"}
    )
    assert res_a.status_code == 429

    # Verifica que rate_user_b consegue logar perfeitamente vindo de outro IP
    res_b = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_b", "password": "pass_b"},
        headers={"X-Forwarded-For": "198.51.100.20"}
    )
    assert res_b.status_code == 200

def test_login_lockout_by_ip(rate_client):
    """Garante que múltiplas falhas vindas do mesmo IP bloqueiam novas tentativas a partir daquele IP."""
    # Envia 5 tentativas malsucedidas para usuários diferentes vindo do mesmo IP
    for i in range(5):
        rate_client.post(
            "/auth/login",
            data={"username": f"random_user_{i}", "password": "wrong_password"},
            headers={"X-Forwarded-For": "198.51.100.50"}
        )

    # Qualquer tentativa nova vinda do mesmo IP deve ser barrada imediatamente (429)
    res = rate_client.post(
        "/auth/login",
        data={"username": "fresh_user", "password": "any_password"},
        headers={"X-Forwarded-For": "198.51.100.50"}
    )
    assert res.status_code == 429
    assert "muitas tentativas" in res.json()["detail"].lower()

def test_login_lockout_expiration(rate_client):
    """Garante que após passar o tempo de lockout (LOGIN_LOCKOUT_MINUTES), o login é liberado."""
    db = TestingSessionLocal()
    from backend.app.security.auth import hash_password
    user = User(username="rate_user_exp", hashed_password=hash_password("correct_password"))
    db.add(user)
    db.commit()

    # Bloqueia o usuário
    for _ in range(5):
        rate_client.post(
            "/auth/login",
            data={"username": "rate_user_exp", "password": "wrong_password"}
        )

    # Confirma que está bloqueado
    res = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_exp", "password": "correct_password"}
    )
    assert res.status_code == 429

    # Altera created_at das tentativas para simular passagem de tempo (+16 minutos)
    attempts = db.query(AuthAttempt).all()
    for attempt in attempts:
        attempt.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.commit()

    # Tenta novamente, agora deve permitir e obter sucesso
    res_success = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_exp", "password": "correct_password"}
    )
    assert res_success.status_code == 200
    assert "access_token" in res_success.json()

def test_login_success_does_not_affect_lockout(rate_client):
    """Garante que login correto de primeira não gera restrição de lockout."""
    db = TestingSessionLocal()
    from backend.app.security.auth import hash_password
    user = User(username="rate_user_clean", hashed_password=hash_password("correct_password"))
    db.add(user)
    db.commit()

    res = rate_client.post(
        "/auth/login",
        data={"username": "rate_user_clean", "password": "correct_password"}
    )
    assert res.status_code == 200

def test_register_lockout_by_ip_and_expiration(rate_client):
    """Garante rate limiting na rota de registro com X-Admin-Key incorreto e posterior expiração."""
    db = TestingSessionLocal()

    # 1. 5 tentativas de registro com X-Admin-Key errada
    for _ in range(5):
        res = rate_client.post(
            "/auth/register",
            json={"username": "new_admin_user", "password": "password123"},
            headers={"X-Admin-Key": "wrong-key", "X-Forwarded-For": "198.51.100.99"}
        )
        assert res.status_code == 401

    # 2. A 6ª tentativa, mesmo com a chave CORRETA, deve retornar 429
    res_lock = rate_client.post(
        "/auth/register",
        json={"username": "new_admin_user", "password": "password123"},
        headers={"X-Admin-Key": "test-admin-secret-key-rate", "X-Forwarded-For": "198.51.100.99"}
    )
    assert res_lock.status_code == 429

    # 3. Simula passagem de tempo das tentativas de registro
    attempts = db.query(AuthAttempt).all()
    for attempt in attempts:
        attempt.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.commit()

    # 4. Tenta novamente com a chave CORRETA após expiração, deve retornar 201
    res_success = rate_client.post(
        "/auth/register",
        json={"username": "new_admin_user", "password": "password123"},
        headers={"X-Admin-Key": "test-admin-secret-key-rate", "X-Forwarded-For": "198.51.100.99"}
    )
    assert res_success.status_code == 201
    assert res_success.json()["username"] == "new_admin_user"

