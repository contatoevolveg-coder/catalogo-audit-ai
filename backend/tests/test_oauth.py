import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet

from backend.app.main import app
from backend.app.database import Base, get_db, Credential, OAuthState
from backend.app.security.crypto import encrypt_secret, decrypt_secret
from backend.app import config

TEST_DB_PATH = "test_temp_oauth.db"
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
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")

@pytest.fixture
def auth_headers(client):
    # Vamos usar o client do conftest (AuthTestClient) que já tem o header auth_headers.
    # Mas como client aqui vai pegar do conftest, ele já tem o token, nós só precisamos do dictionary.
    return client.auth_headers

def test_start_authorization_success(client, auth_headers):
    db = TestingSessionLocal()
    with patch("backend.app.services.oauth_service.ML_CLIENT_ID", "mock_ml_id"):
        response = client.get("/oauth/mercado_livre/authorize", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "authorization_url" in data
    assert "https://auth.mercadolivre.com.br/authorization" in data["authorization_url"]
    
    # Verifica se salvou o state no banco
    db_state = db.query(OAuthState).filter(OAuthState.provider == "mercado_livre").first()
    assert db_state is not None
    assert db_state.state in data["authorization_url"]
    db.close()

def test_start_authorization_invalid_provider(client, auth_headers):
    response = client.get("/oauth/fake_provider/authorize", headers=auth_headers)
    assert response.status_code == 400
    assert "Provedor inválido" in response.json()["detail"]

def test_callback_success(client, auth_headers):
    db = TestingSessionLocal()
    # 1. Cria o State
    state_val = "test_state_123"
    db_state = OAuthState(state=state_val, provider="bling")
    db.add(db_state)
    db.commit()

    # 2. Mock o exchange
    mock_body = {
        "access_token": "bling_mock_access_token",
        "refresh_token": "bling_mock_refresh_token",
        "expires_in": 3600
    }
    
    with patch("backend.app.integrations.bling.exchange_code_for_token", return_value=(True, mock_body)):
        with patch("backend.app.services.oauth_service.BLING_CLIENT_ID", "mock_id"):
            with patch("backend.app.services.oauth_service.BLING_CLIENT_SECRET", "mock_secret"):
                response = client.post(
                    "/oauth/bling/callback",
                    json={
                        "code": "test_auth_code",
                        "state": state_val,
                        "label": "Minha Conta Bling"
                    },
                    headers=auth_headers
                )
    
    if response.status_code != 200:
        print(response.json())
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "bling"
    assert data["label"] == "Minha Conta Bling"
    assert data["status"] == "valid"
    assert "token_expires_at" in data
    
    # Confirma que a credencial está no banco
    cred = db.query(Credential).filter(Credential.id == data["id"]).first()
    assert cred is not None
    assert cred.token_expires_at is not None
    
    # Confirma que o state foi consumido
    assert db.query(OAuthState).filter(OAuthState.state == state_val).first() is None
    db.close()

def test_callback_invalid_state(client, auth_headers):
    response = client.post(
        "/oauth/mercado_livre/callback",
        json={
            "code": "test_auth_code",
            "state": "state_nao_existente",
            "label": "Minha Conta"
        },
        headers=auth_headers
    )
    assert response.status_code == 400
    assert "State inválido ou expirado" in response.json()["detail"]

def test_refresh_if_needed_not_needed():
    """Testa que refresh_if_needed retorna a credencial sem alterar se não está perto de expirar."""
    import datetime
    from backend.app.services.oauth_service import refresh_if_needed
    db = TestingSessionLocal()
    
    # Expira daqui a 2 horas (muito além de 5 minutos)
    future = datetime.datetime.utcnow() + datetime.timedelta(hours=2)
    cred = Credential(
        provider="mercado_livre",
        provider_type="marketplace",
        label="Test ML",
        encrypted_secret="fake_secret",
        masked_preview="fake",
        scopes=["read"],
        status="valid",
        token_expires_at=future
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    
    updated_cred = refresh_if_needed(cred, db)
    assert updated_cred.encrypted_secret == "fake_secret" # Não mudou
    db.close()

def test_refresh_if_needed_triggers_refresh():
    """Testa o comportamento de renovação quando o token está expirado/prestes a expirar."""
    import datetime
    from backend.app.services.oauth_service import refresh_if_needed
    db = TestingSessionLocal()
    
    # Expira no passado (já expirado)
    past = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
    
    secret_payload = {
        "access_token": "old_access",
        "refresh_token": "old_refresh"
    }
    encrypted = encrypt_secret(secret_payload)
    
    cred = Credential(
        provider="mercado_livre",
        provider_type="marketplace",
        label="Test ML",
        encrypted_secret=encrypted,
        masked_preview="fake",
        scopes=["read"],
        status="valid",
        token_expires_at=past
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    
    mock_body = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "expires_in": 3600
    }
    
    with patch("backend.app.integrations.mercado_livre.refresh_access_token", return_value=(True, mock_body)):
        updated_cred = refresh_if_needed(cred, db)
        
    assert updated_cred.status == "valid"
    assert updated_cred.token_expires_at > datetime.datetime.utcnow()
    assert "cess" in updated_cred.masked_preview
    
    # Confirma que descriptografando tem os novos dados
    new_secret = decrypt_secret(updated_cred.encrypted_secret)
    assert new_secret["access_token"] == "new_access"
    assert new_secret["refresh_token"] == "new_refresh"
    db.close()

