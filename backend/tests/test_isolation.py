import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime

from backend.app.main import app
from backend.app.database import User, Tenant, Product, Credential, Base, engine, get_db
from backend.app.security.auth import create_access_token, hash_password

# Configuração de teste em memória já feita no conftest se usar os mesmos fixtures,
# mas para isolamento explícito, criamos usuários e tenants diretamente no DB.

@pytest.fixture
def isolation_setup(db_session: Session):
    # Cria Tenant A e Usuário A
    tenant_a = Tenant(name="Tenant A")
    db_session.add(tenant_a)
    db_session.flush()

    user_a = User(username="user_a", hashed_password=hash_password("senha123"), role="admin", is_active=True, tenant_id=tenant_a.id)
    db_session.add(user_a)

    # Cria Tenant B e Usuário B
    tenant_b = Tenant(name="Tenant B")
    db_session.add(tenant_b)
    db_session.flush()

    user_b = User(username="user_b", hashed_password=hash_password("senha123"), role="admin", is_active=True, tenant_id=tenant_b.id)
    db_session.add(user_b)

    # Cria Produto no Tenant A
    prod_a = Product(
        tenant_id=tenant_a.id,
        title="Produto A",
        price=10.0,
        marketplace="mercado_livre",
        status="pending"
    )
    db_session.add(prod_a)

    # Cria Produto no Tenant B
    prod_b = Product(
        tenant_id=tenant_b.id,
        title="Produto B",
        price=20.0,
        marketplace="mercado_livre",
        status="pending"
    )
    db_session.add(prod_b)

    db_session.commit()

    return {
        "tenant_a": tenant_a,
        "user_a": user_a,
        "token_a": create_access_token("user_a"),
        "prod_a": prod_a,
        "tenant_b": tenant_b,
        "user_b": user_b,
        "token_b": create_access_token("user_b"),
        "prod_b": prod_b
    }

def test_tenant_isolation_products(client: TestClient, isolation_setup: dict):
    """Garante que o Usuário A só vê produtos do Tenant A e não os do B."""
    # Auth as User A
    headers_a = {"Authorization": f"Bearer {isolation_setup['token_a']}"}
    
    resp_a = client.get("/products", headers=headers_a)
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    
    # Verifica que só retornou o produto do Tenant A
    assert len(data_a["items"]) >= 1
    titles_a = [p["title"] for p in data_a["items"]]
    assert "Produto A" in titles_a
    assert "Produto B" not in titles_a

    # Tenta acessar Produto B diretamente pelo ID (deverá falhar)
    prod_b_id = isolation_setup['prod_b'].id
    resp_a_direct = client.get(f"/products/{prod_b_id}", headers=headers_a)
    assert resp_a_direct.status_code == 404

    # Auth as User B
    headers_b = {"Authorization": f"Bearer {isolation_setup['token_b']}"}
    
    resp_b = client.get("/products", headers=headers_b)
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    
    # Verifica que só retornou o produto do Tenant B
    assert len(data_b["items"]) >= 1
    titles_b = [p["title"] for p in data_b["items"]]
    assert "Produto B" in titles_b
    assert "Produto A" not in titles_b

def test_tenant_isolation_credentials(client: TestClient, db_session: Session, isolation_setup: dict):
    """Garante que o Usuário A só vê suas próprias credenciais."""
    # Cria Credencial no Tenant B
    cred_b = Credential(
        tenant_id=isolation_setup["tenant_b"].id,
        provider="mercado_livre",
        provider_type="marketplace",
        label="Credencial B",
        encrypted_secret="enc_secret",
        masked_preview="Oauth...",
        status="valid",
        scopes=[]
    )
    db_session.add(cred_b)
    db_session.commit()

    headers_a = {"Authorization": f"Bearer {isolation_setup['token_a']}"}
    resp_a = client.get("/credentials", headers=headers_a)
    assert resp_a.status_code == 200
    
    labels_a = [c["label"] for c in resp_a.json()]
    assert "Credencial B" not in labels_a

    # Tenta acessar diretamente a credencial B com token do A
    resp_direct = client.get(f"/credentials/{cred_b.id}", headers=headers_a)
    assert resp_direct.status_code == 404
