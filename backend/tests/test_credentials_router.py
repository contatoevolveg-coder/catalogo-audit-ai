import os
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Credential, ExternalCallLog
from backend.app import config
from cryptography.fernet import Fernet

TEST_DB_PATH = "test_temp_credentials.db"
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


@pytest.fixture(autouse=True)
def setup_keys(monkeypatch):
    """Configura chaves administrativas e de criptografia válidas para os testes normais."""
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")

def test_credentials_jwt_secret_not_configured(monkeypatch):
    """Garante que endpoints retornam 503 se JWT_SECRET_KEY não estiver configurada no ambiente."""
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "")
    with TestClient(app) as c:
        response = c.get("/credentials", headers={"Authorization": "Bearer anytoken"})
        assert response.status_code == 503
        assert "não configurada" in response.json()["detail"].lower()

def test_credentials_unauthorized(client):
    """Garante que endpoints retornam 401 se JWT estiver ausente ou inválido."""
    # 1. Sem header Authorization
    response = client.get("/credentials", headers={"skip_auth": True})
    assert response.status_code == 401
    
    # 2. Com token inválido
    response = client.get("/credentials", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401


def test_create_credential_success(client):
    """Garante criação correta de credencial, preview mascarado, e que o segredo bruto não vaza."""
    secret = {"access_token": "APP_USR-987654321-XYZ", "client_secret": "my-secret-value-abc"}
    payload = {
        "provider": "mercado_livre",
        "provider_type": "marketplace",
        "label": "ML Principal",
        "secret_payload": secret,
        "scopes": ["read_products", "write_price"]
    }
    
    response = client.post(
        "/credentials",
        json=payload,
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "mercado_livre"
    assert data["masked_preview"] == "••••-XYZ"
    assert data["status"] == "untested"
    
    # Assegura que o token/segredo original nunca vaza na resposta do JSON de forma literal
    assert "APP_USR-987654321-XYZ" not in response.text
    assert "my-secret-value-abc" not in response.text

def test_create_credential_invalid_scopes(client):
    """Garante que escopos fora do ALLOWED_CREDENTIAL_SCOPES retornam erro 422."""
    payload = {
        "provider": "mercado_livre",
        "provider_type": "marketplace",
        "label": "ML Principal",
        "secret_payload": {"access_token": "xyz"},
        "scopes": ["read_products", "escopo_malicioso_invalido"]
    }
    
    response = client.post(
        "/credentials",
        json=payload,
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert response.status_code == 422
    assert "não é permitido" in response.text

def test_list_credentials(client):
    """Garante que a listagem de credenciais oculta completamente chaves sensíveis."""
    secret = {"access_token": "TOKEN-SECRET-ML-1234"}
    client.post(
        "/credentials",
        json={
            "provider": "mercado_livre",
            "provider_type": "marketplace",
            "label": "ML Prod",
            "secret_payload": secret,
            "scopes": ["read_products"]
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    
    response = client.get("/credentials", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["masked_preview"] == "••••1234"
    assert "TOKEN-SECRET-ML-1234" not in response.text

def test_connectivity_check_mercado_livre(client, monkeypatch):
    """Garante a checagem de conectividade do Mercado Livre contra mock da API oficial."""
    # Cria a credencial
    create_res = client.post(
        "/credentials",
        json={
            "provider": "mercado_livre",
            "provider_type": "marketplace",
            "label": "ML Test Connection",
            "secret_payload": {"access_token": "APP_USR-TOKEN"},
            "scopes": ["read_products"]
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    ).json()
    cred_id = create_res["id"]

    # Caso 1: API oficial retorna 200 (Válido)
    class MockResponse200:
        status_code = 200
        
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse200())
    test_res = client.post(f"/credentials/{cred_id}/test", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert test_res.status_code == 200
    assert test_res.json()["status"] == "valid"
    assert test_res.json()["status_detail"] is None
    
    # Verifica que ExternalCallLog foi gerado sem o token nos detalhes
    db = TestingSessionLocal()
    log = db.query(ExternalCallLog).filter(ExternalCallLog.kind == "credential_check").first()
    assert log is not None
    assert log.success is True
    # O token sensível NÃO pode estar no detail
    assert "APP_USR-TOKEN" not in str(log.detail)
    db.close()

    # Caso 2: API oficial retorna 401 (Expirado)
    class MockResponse401:
        status_code = 401
        
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse401())
    test_res_exp = client.post(f"/credentials/{cred_id}/test", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert test_res_exp.json()["status"] == "expired"
    assert "Token inválido" in test_res_exp.json()["status_detail"]

    # Caso 3: Erro de rede (Error)
    def mock_get_error(*args, **kwargs):
        raise httpx.RequestError("Conexão recusada")
        
    monkeypatch.setattr(httpx, "get", mock_get_error)
    test_res_err = client.post(f"/credentials/{cred_id}/test", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert test_res_err.json()["status"] == "error"
    assert "Conexão recusada" in test_res_err.json()["status_detail"]

def test_connectivity_check_fallback_untested(client):
    """Garante que conexões para provedores não implementados retornam untested."""
    create_res = client.post(
        "/credentials",
        json={
            "provider": "shopee",
            "provider_type": "marketplace",
            "label": "Shopee Test Connection",
            "secret_payload": {"api_key": "shopee-token"},
            "scopes": ["read_products"]
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    ).json()
    cred_id = create_res["id"]

    test_res = client.post(f"/credentials/{cred_id}/test", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert test_res.status_code == 200
    assert test_res.json()["status"] == "untested"
    assert "não implementada" in test_res.json()["status_detail"]

def test_patch_credential_rotation(client):
    """Garante rotação do segredo recriptografando o novo payload e mudando o hash do banco."""
    # 1. Cria a credencial
    create_res = client.post(
        "/credentials",
        json={
            "provider": "mercado_livre",
            "provider_type": "marketplace",
            "label": "ML Principal",
            "secret_payload": {"access_token": "TOKEN-ORIGINAL"},
            "scopes": ["read_products"]
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    ).json()
    cred_id = create_res["id"]
    
    db = TestingSessionLocal()
    cred_before = db.query(Credential).filter(Credential.id == cred_id).first()
    encrypted_before = cred_before.encrypted_secret
    db.close()

    # 2. Rotaciona via PATCH
    patch_res = client.patch(
        f"/credentials/{cred_id}",
        json={
            "label": "ML Rotacionada",
            "secret_payload": {"access_token": "NOVO-TOKEN-ROTA-9999"}
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["label"] == "ML Rotacionada"
    assert patch_res.json()["masked_preview"] == "••••9999"

    # 3. Assegura que o encrypted_secret no banco fisicamente mudou
    db = TestingSessionLocal()
    cred_after = db.query(Credential).filter(Credential.id == cred_id).first()
    assert cred_after.encrypted_secret != encrypted_before
    db.close()

def test_delete_credential(client):
    """Garante que a exclusão remove fisicamente o registro e gera 404 em consultas."""
    create_res = client.post(
        "/credentials",
        json={
            "provider": "shopee",
            "provider_type": "marketplace",
            "label": "Shopee Temp",
            "secret_payload": {"api_key": "temp"},
            "scopes": ["read_products"]
        },
        headers={"X-Admin-Key": "test-admin-secret-key"}
    ).json()
    cred_id = create_res["id"]

    # Exclui
    del_res = client.delete(f"/credentials/{cred_id}", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert del_res.status_code == 200

    # Tenta obter
    get_res = client.get(f"/credentials/{cred_id}", headers={"X-Admin-Key": "test-admin-secret-key"})
    assert get_res.status_code == 404
