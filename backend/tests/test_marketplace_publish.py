import os
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Product, Suggestion, Credential, MarketplacePublication
from backend.app.schemas import CredentialCreate
from backend.app.services import credential_service
from backend.app import config
from cryptography.fernet import Fernet

TEST_DB_PATH = "test_temp_publish.db"
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

def test_publish_endpoints_guard(client):
    """Garante que qualquer chamada sem JWT válido retorne 401."""
    # 1. Category Suggestions
    res1 = client.get(
        "/marketplace-integrations/mercado_livre/category-suggestions?title=celular",
        headers={"skip_auth": True}
    )
    assert res1.status_code == 401

    # 2. Publish product
    res2 = client.post(
        "/marketplace-integrations/products/1/publish",
        json={"credential_id": 1, "category_id": "MLB1051"},
        headers={"skip_auth": True}
    )
    assert res2.status_code == 401

    # 3. History
    res3 = client.get(
        "/marketplace-integrations/products/1/publications",
        headers={"skip_auth": True}
    )
    assert res3.status_code == 401


def test_publish_happy_path(client, monkeypatch):
    """Fluxo feliz: produto auditado + sugestão aprovada + credencial ML válida -> publicação de sucesso."""
    db = TestingSessionLocal()
    
    # 1. Cria produto no banco
    product = Product(
        title="Celular Apple iPhone 13 128GB",
        description="Original semi-novo.",
        price=3500.0,
        marketplace="mercado_livre",
        status="pending",
        available_quantity=2,
        condition="new",
        images=["https://images.unsplash.com/photo-1"],
        attributes={"brand": "Apple", "model": "iPhone 13", "gtin_ean": "7891234567890"}
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    
    # 2. Adiciona a sugestão aprovada
    suggestion = Suggestion(
        product_id=product.id,
        suggested_title="Celular Apple iPhone 13 128GB Lacrado",
        suggested_description="iPhone 13 128GB novo em folha.",
        seo_score=95,
        status="approved"
    )
    db.add(suggestion)
    db.commit()

    # 3. Cria credencial válida
    cred_data = CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste Real",
        secret_payload={"access_token": "MY-MOCKED-BEARER-TOKEN-999"},
        scopes=["read_products", "write_listing"]
    )
    cred = credential_service.create_credential(db, cred_data)
    cred.status = "valid"
    db.commit()
    db.refresh(cred)
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock das chamadas HTTP da API do Mercado Livre
    calls_made = []

    def mock_post(url, headers, json, timeout):
        calls_made.append({"url": url, "json": json, "headers": headers})
        if "/items" in url and "/description" not in url:
            # POST /items
            assert headers["Authorization"] == "Bearer MY-MOCKED-BEARER-TOKEN-999"
            return httpx.Response(201, json={"id": "MLB999"})
        elif "/description" in url:
            # POST /items/MLB999/description
            assert headers["Authorization"] == "Bearer MY-MOCKED-BEARER-TOKEN-999"
            assert json["plain_text"] == "iPhone 13 128GB novo em folha."
            return httpx.Response(200, json={"status": "updated"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "post", mock_post)

    # 4. Executa a publicação
    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_id, "category_id": "MLB1051"},
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["marketplace_item_id"] == "MLB999"
    assert data["category_id"] == "MLB1051"

    # Verifica segurança: access_token não pode vazar na resposta JSON
    assert "MY-MOCKED-BEARER-TOKEN-999" not in response.text

    # Verifica atualização no banco
    db = TestingSessionLocal()
    updated_product = db.query(Product).filter(Product.id == product_id).first()
    assert updated_product.status == "published"
    assert updated_product.external_listing_id == "MLB999"
    
    # Verifica histórico de publicações
    history = db.query(MarketplacePublication).filter(MarketplacePublication.product_id == product_id).all()
    assert len(history) == 1
    assert history[0].status == "success"
    assert history[0].marketplace_item_id == "MLB999"
    # O payload salvo não pode conter o token
    assert "MY-MOCKED-BEARER-TOKEN-999" not in str(history[0].request_payload)
    db.close()

def test_publish_blocked_by_pending_suggestion(client):
    """Garante bloqueio de publicação 400 se a sugestão mais recente não estiver aprovada."""
    db = TestingSessionLocal()
    # Produto com sugestão pendente
    product = Product(
        title="Copo Stanley",
        marketplace="mercado_livre",
        status="pending"
    )
    db.add(product)
    db.commit()
    
    suggestion = Suggestion(
        product_id=product.id,
        suggested_title="Copo Térmico",
        suggested_description="Desc",
        seo_score=80,
        status="pending"
    )
    db.add(suggestion)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Principal",
        secret_payload={"access_token": "token"},
        scopes=["read_products"]
    ))
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Dispara publish sem mockar httpx (não deve sequer tentar disparar requisição)
    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_id, "category_id": "MLB1051"},
        headers=headers
    )
    assert response.status_code == 400
    assert "sugestão aprovada antes de ser publicado" in response.json()["detail"]

def test_publish_blocked_by_invalid_credential(client):
    """Garante bloqueio 400 se a credencial não for válida ou for de outro provedor."""
    db = TestingSessionLocal()
    product = Product(
        title="Caneca",
        marketplace="mercado_livre",
        status="pending"
    )
    db.add(product)
    db.commit()
    
    suggestion = Suggestion(
        product_id=product.id,
        suggested_title="Caneca Térmica",
        suggested_description="Desc",
        seo_score=80,
        status="approved"
    )
    db.add(suggestion)
    
    # Caso 1: Provedor errado
    cred_shopee = credential_service.create_credential(db, CredentialCreate(
        provider="shopee",
        provider_type="marketplace",
        label="Shopee",
        secret_payload={"api_key": "token"},
        scopes=["read_products"]
    ))
    cred_shopee.status = "valid"
    
    # Caso 2: Provedor correto mas expirado
    cred_ml_exp = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML",
        secret_payload={"access_token": "token"},
        scopes=["read_products"]
    ))
    cred_ml_exp.status = "expired"
    db.commit()
    
    product_id = product.id
    cred_shopee_id = cred_shopee.id
    cred_ml_exp_id = cred_ml_exp.id
    db.close()

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    
    # 1. Provedor shopee -> 400
    res1 = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_shopee_id, "category_id": "1051"},
        headers=headers
    )
    assert res1.status_code == 400
    assert "Token de acesso ou shop_id ausentes" in res1.json()["detail"]

    # 2. Expirado -> 401 (agora retorna 401 via refresh_if_needed)
    res2 = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_ml_exp_id, "category_id": "MLB1051"},
        headers=headers
    )
    assert res2.status_code == 401
    assert "Falha na renovação da credencial" in res2.json()["detail"]

def test_publish_token_expired_401(client, monkeypatch):
    """Garante que erro 401 do ML altera a credencial para 'expired' e retorna 502."""
    db = TestingSessionLocal()
    product = Product(
        title="Caneca",
        marketplace="mercado_livre",
        status="pending"
    )
    db.add(product)
    db.commit()
    
    suggestion = Suggestion(
        product_id=product.id,
        suggested_title="Caneca Térmica",
        suggested_description="Desc",
        seo_score=80,
        status="approved"
    )
    db.add(suggestion)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML",
        secret_payload={"access_token": "MY-EXPIRED-TOKEN"},
        scopes=["read_products"]
    ))
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock retorna 401
    monkeypatch.setattr(
        httpx, "post",
        lambda *args, **kwargs: httpx.Response(401, json={"message": "invalid_token", "error": "unauthorized", "status": 401})
    )

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_id, "category_id": "MLB1051"},
        headers=headers
    )
    
    assert response.status_code == 502
    assert "token de acesso" in response.json()["detail"].lower() or "http 401" in response.json()["detail"].lower()

    # Certifica segurança: token expirado não vaza na resposta
    assert "MY-EXPIRED-TOKEN" not in response.text

    # Verifica alteração do status da credencial
    db = TestingSessionLocal()
    updated_cred = db.query(Credential).filter(Credential.id == cred_id).first()
    assert updated_cred.status == "expired"
    
    # Produto não deve estar publicado
    updated_prod = db.query(Product).filter(Product.id == product_id).first()
    assert updated_prod.status != "published"
    db.close()

def test_publish_validation_error_400(client, monkeypatch):
    """Garante que erro 400 (ex.: categoria/atributos incorretos) retorna 502 com detalhes do ML."""
    db = TestingSessionLocal()
    product = Product(
        title="Caneca",
        marketplace="mercado_livre",
        status="pending"
    )
    db.add(product)
    db.commit()
    
    suggestion = Suggestion(
        product_id=product.id,
        suggested_title="Caneca Térmica",
        suggested_description="Desc",
        seo_score=80,
        status="approved"
    )
    db.add(suggestion)
    
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML",
        secret_payload={"access_token": "MY-TOKEN"},
        scopes=["read_products"]
    ))
    cred.status = "valid"
    db.commit()
    
    product_id = product.id
    cred_id = cred.id
    db.close()

    # Mock retorna 400 com corpo de erro estruturado do ML
    ml_error = {"message": "validation error: attribute BRAND is missing", "error": "validation_error", "status": 400}
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(400, json=ml_error))

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.post(
        f"/marketplace-integrations/products/{product_id}/publish",
        json={"credential_id": cred_id, "category_id": "MLB1051"},
        headers=headers
    )
    
    assert response.status_code == 502
    assert "attribute BRAND is missing" in response.json()["detail"]

    # Verifica segurança
    assert "MY-TOKEN" not in response.text

    # Verifica banco de dados
    db = TestingSessionLocal()
    history = db.query(MarketplacePublication).filter(MarketplacePublication.product_id == product_id).first()
    assert history.status == "error"
    assert "BRAND is missing" in history.error_detail
    
    updated_prod = db.query(Product).filter(Product.id == product_id).first()
    assert updated_prod.status != "published"
    db.close()

def test_category_suggestions_success(client, monkeypatch):
    """Garante que GET /category-suggestions retorne lista formatada de forma correta."""
    mock_data = [
        {"id": "MLB1051", "name": "Celulares e Smartphones"},
        {"id": "MLB1052", "name": "Acessórios para Celulares"}
    ]
    
    monkeypatch.setattr(
        httpx, "get",
        lambda *args, **kwargs: httpx.Response(200, json=mock_data)
    )

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.get(
        "/marketplace-integrations/mercado_livre/category-suggestions?title=celular",
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["category_id"] == "MLB1051"
    assert data[0]["category_name"] == "Celulares e Smartphones"

def test_category_suggestions_network_error(client, monkeypatch):
    """Garante que erro de rede na API de categoria retorne uma lista vazia, sem lançar HTTP 500."""
    def mock_get_error(*args, **kwargs):
        raise httpx.RequestError("Servidor fora do ar")

    monkeypatch.setattr(httpx, "get", mock_get_error)

    headers = {"X-Admin-Key": "test-admin-secret-key"}
    response = client.get(
        "/marketplace-integrations/mercado_livre/category-suggestions?title=celular",
        headers=headers
    )
    
    assert response.status_code == 200
    assert response.json() == []
