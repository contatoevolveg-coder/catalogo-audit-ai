import os
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Product, Credential, ErpSyncLog
from backend.app.schemas import CredentialCreate
from backend.app.services import credential_service
from backend.app import config
from cryptography.fernet import Fernet

TEST_DB_PATH = "test_temp_bling.db"
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

def test_bling_endpoints_guard(client):
    """Garante que chamadas sem JWT válido retornem 401."""
    res1 = client.patch(
        "/erp-integrations/bling/products/1/erp-link",
        json={"erp_sku": "SKU123"},
        headers={"skip_auth": True}
    )
    assert res1.status_code == 401

    res2 = client.post(
        "/erp-integrations/bling/products/1/sync-stock",
        json={"credential_id": 1},
        headers={"skip_auth": True}
    )
    assert res2.status_code == 401

    res3 = client.post(
        "/erp-integrations/bling/sync-all",
        json={"credential_id": 1},
        headers={"skip_auth": True}
    )
    assert res3.status_code == 401

    res4 = client.get(
        "/erp-integrations/bling/products/1/sync-history",
        headers={"skip_auth": True}
    )
    assert res4.status_code == 401


def test_link_erp_sku(client):
    """Garante o vínculo de SKU ao produto local via PATCH."""
    db = TestingSessionLocal()
    product = Product(
        tenant_id=1,
        title="Teclado Mecânico Gamer",
        marketplace="mercado_livre",
        status="pending"
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    product_id = product.id
    db.close()

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.patch(
        f"/erp-integrations/bling/products/{product_id}/erp-link",
        json={"erp_sku": "TEC-GAMER-888"},
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["erp_sku"] == "TEC-GAMER-888"

    # Confirma persistência
    db = TestingSessionLocal()
    updated = db.query(Product).filter(Product.id == product_id).first()
    assert updated.erp_sku == "TEC-GAMER-888"
    db.close()

def test_sync_stock_success(client, monkeypatch):
    """Garante sincronização com sucesso buscando o produto no Bling por SKU e depois seu estoque."""
    db = TestingSessionLocal()
    product = Product(
        tenant_id=1,
        title="Fone Bluetooth",
        marketplace="mercado_livre",
        status="pending",
        available_quantity=5,
        erp_sku="FONE-BLUE-123"
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling Principal",
        secret_payload={"access_token": "MY-BLING-BEARER-TOKEN-777"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    db.refresh(cred)
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock das chamadas HTTP da API do Bling v3
    calls = []
    def mock_get(url, headers, params=None, timeout=5.0):
        calls.append({"url": url, "headers": headers, "params": params})
        assert headers["Authorization"] == "Bearer MY-BLING-BEARER-TOKEN-777"
        
        if "/produtos" in url:
            # Consulta por SKU
            assert params["codigo"] == "FONE-BLUE-123"
            return httpx.Response(200, json={
                "data": [
                    {"id": 987654321, "codigo": "FONE-BLUE-123", "nome": "Fone Bluetooth"}
                ]
            })
        elif "/estoques/saldos" in url:
            # Consulta de estoque por ID do produto
            assert params["idsProdutos[]"] == 987654321
            return httpx.Response(200, json={
                "data": [
                    {
                        "produto": {"id": 987654321},
                        "saldoFisico": 42,
                        "saldoVirtual": 40
                    }
                ]
            })
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "get", mock_get)

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/erp-integrations/bling/products/{product_id}/sync-stock",
        json={"credential_id": cred_id},
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["previous_quantity"] == 5
    assert data["new_quantity"] == 42

    # Verifica segurança: token não vaza no JSON de resposta
    assert "MY-BLING-BEARER-TOKEN-777" not in response.text

    # Verifica persistência local
    db = TestingSessionLocal()
    updated = db.query(Product).filter(Product.id == product_id).first()
    assert updated.available_quantity == 42
    
    logs = db.query(ErpSyncLog).filter(ErpSyncLog.product_id == product_id).all()
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].new_quantity == 42
    assert "MY-BLING-BEARER-TOKEN-777" not in str(logs[0].response_payload)
    db.close()

def test_sync_stock_blocked_by_missing_sku(client):
    """Garante bloqueio 400 antes de acionar chamada externa caso o SKU não esteja vinculado."""
    db = TestingSessionLocal()
    product = Product(
        tenant_id=1,
        title="Fone Sem SKU",
        marketplace="mercado_livre",
        status="pending",
        erp_sku=None  # Sem SKU!
    )
    db.add(product)
    db.commit()
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling",
        secret_payload={"access_token": "token"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/erp-integrations/bling/products/{product_id}/sync-stock",
        json={"credential_id": cred_id},
        headers=headers
    )
    
    assert response.status_code == 400
    assert "não possui SKU do Bling vinculado" in response.json()["detail"]

def test_sync_stock_sku_not_found_on_bling(client, monkeypatch):
    """Garante gravação do status 'not_found' caso o SKU não exista no Bling."""
    db = TestingSessionLocal()
    product = Product(
        tenant_id=1,
        title="Item Raro",
        marketplace="mercado_livre",
        status="pending",
        available_quantity=10,
        erp_sku="RARITY-999"
    )
    db.add(product)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling",
        secret_payload={"access_token": "MY-TOKEN"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock retorna 200 mas lista vazia de produtos
    monkeypatch.setattr(
        httpx, "get",
        lambda *args, **kwargs: httpx.Response(200, json={"data": []})
    )

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/erp-integrations/bling/products/{product_id}/sync-stock",
        json={"credential_id": cred_id},
        headers=headers
    )
    
    assert response.status_code == 404
    assert "Nenhum produto encontrado com o SKU" in response.json()["detail"]

    # Verifica que quantidade de estoque local não mudou
    db = TestingSessionLocal()
    prod = db.query(Product).filter(Product.id == product_id).first()
    assert prod.available_quantity == 10
    
    log = db.query(ErpSyncLog).filter(ErpSyncLog.product_id == product_id).first()
    assert log.status == "not_found"
    db.close()

def test_sync_stock_token_expired_401(client, monkeypatch):
    """Garante que erro 401 do Bling muda credencial para 'expired' no banco e retorna 502."""
    db = TestingSessionLocal()
    product = Product(
        tenant_id=1,
        title="Item",
        marketplace="mercado_livre",
        status="pending",
        available_quantity=10,
        erp_sku="SKU-401"
    )
    db.add(product)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling",
        secret_payload={"access_token": "MY-EXPIRED-TOKEN-BLING"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock retorna 401
    monkeypatch.setattr(
        httpx, "get",
        lambda *args, **kwargs: httpx.Response(401, json={"error": {"message": "Unauthorized"}})
    )

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/erp-integrations/bling/products/{product_id}/sync-stock",
        json={"credential_id": cred_id},
        headers=headers
    )
    
    assert response.status_code == 502
    assert "expirado" in response.json()["detail"].lower()

    # Verifica segurança
    assert "MY-EXPIRED-TOKEN-BLING" not in response.text

    # Verifica banco
    db = TestingSessionLocal()
    updated_cred = db.query(Credential).filter(Credential.id == cred_id).first()
    assert updated_cred.status == "expired"
    
    log = db.query(ErpSyncLog).filter(ErpSyncLog.product_id == product_id).first()
    assert log.status == "error"
    db.close()

def test_bulk_sync_cap_limit(client, monkeypatch):
    """Garante que o sync-all respeita o limite de max_sync do lote."""
    db = TestingSessionLocal()
    # Cria 3 produtos com erp_sku vinculados
    for i in range(1, 4):
        p = Product(
            tenant_id=1,
            title=f"Prod {i}",
            marketplace="mercado_livre",
            status="pending",
            available_quantity=0,
            erp_sku=f"SKU-BULK-{i}"
        )
        db.add(p)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling",
        secret_payload={"access_token": "token"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    cred_id = cred.id
    db.close()

    # Mock Bling para retornar estoque sempre 50
    def mock_get(url, headers, params=None, timeout=5.0):
        if "/produtos" in url:
            return httpx.Response(200, json={"data": [{"id": 123}]})
        elif "/estoques/saldos" in url:
            return httpx.Response(200, json={"data": [{"produto": {"id": 123}, "saldoFisico": 50}]})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "get", mock_get)

    # Executa sync com max_sync = 2
    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        "/erp-integrations/bling/sync-all",
        json={"credential_id": cred_id, "max_sync": 2},
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["synced"] == 2  # Apenas 2 processados de 3 no lote
    assert data["not_found"] == 0
    assert len(data["errors"]) == 0

def test_bulk_sync_resilience(client, monkeypatch):
    """Garante que falhas individuais (ex.: exceção de rede) em um produto não param o processamento dos demais."""
    db = TestingSessionLocal()
    
    p1 = Product(tenant_id=1, title="P1", marketplace="mercado_livre", status="pending", erp_sku="SKU-OK-1")
    p2 = Product(tenant_id=1, title="P2", marketplace="mercado_livre", status="pending", erp_sku="SKU-FAIL")
    p3 = Product(tenant_id=1, title="P3", marketplace="mercado_livre", status="pending", erp_sku="SKU-OK-2")
    db.add_all([p1, p2, p3])
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="bling",
        provider_type="erp",
        label="Bling",
        secret_payload={"access_token": "token"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    
    p1_id = p1.id
    p2_id = p2.id
    p3_id = p3.id
    cred_id = cred.id
    db.close()

    # Mock Bling: SKU-FAIL lança exceção, os demais funcionam
    def mock_get(url, headers, params=None, timeout=5.0):
        if "/produtos" in url:
            codigo = params.get("codigo")
            if codigo == "SKU-FAIL":
                raise httpx.RequestError("Conexão interrompida com o Bling")
            return httpx.Response(200, json={"data": [{"id": 123}]})
        elif "/estoques/saldos" in url:
            return httpx.Response(200, json={"data": [{"produto": {"id": 123}, "saldoFisico": 15}]})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "get", mock_get)

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        "/erp-integrations/bling/sync-all",
        json={"credential_id": cred_id, "max_sync": 10},
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["synced"] == 2  # P1 e P3 sincronizados com sucesso
    assert len(data["errors"]) == 1  # P2 falhou
    assert data["errors"][0]["product_id"] == p2_id
    assert "Conexão interrompida" in data["errors"][0]["error"]
