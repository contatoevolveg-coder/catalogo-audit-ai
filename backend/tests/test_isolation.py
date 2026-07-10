import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Tenant, User, Product, Credential
from backend.app.security.auth import hash_password, create_access_token
from backend.app import config

TEST_DB_PATH = "test_temp_isolation.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    Base.metadata.create_all(bind=engine)

    def test_get_db():
        d = TestingSessionLocal()
        try:
            yield d
        finally:
            d.close()
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
def isolation_setup():
    db = TestingSessionLocal()
    ta = Tenant(name="Tenant A"); db.add(ta); db.flush()
    tb = Tenant(name="Tenant B"); db.add(tb); db.flush()
    db.add(User(tenant_id=ta.id, username="user_a", hashed_password=hash_password("x"), role="admin", is_active=True))
    db.add(User(tenant_id=tb.id, username="user_b", hashed_password=hash_password("x"), role="admin", is_active=True))
    prod_a = Product(tenant_id=ta.id, title="Produto A", price=10.0, marketplace="mercado_livre", status="pending")
    prod_b = Product(tenant_id=tb.id, title="Produto B", price=20.0, marketplace="mercado_livre", status="pending")
    cred_b = Credential(tenant_id=tb.id, provider="mercado_livre", provider_type="marketplace", label="Credencial B",
                        encrypted_secret="enc", masked_preview="•", status="valid", scopes=[])
    db.add_all([prod_a, prod_b, cred_b])
    db.commit()
    data = {
        "token_a": create_access_token("user_a"),
        "token_b": create_access_token("user_b"),
        "prod_a_id": prod_a.id,
        "prod_b_id": prod_b.id,
        "cred_b_id": cred_b.id,
    }
    db.close()
    return data


def test_tenant_isolation_products(isolation_setup):
    client = TestClient(app)
    headers_a = {"Authorization": f"Bearer {isolation_setup['token_a']}"}

    resp_a = client.get("/products", headers=headers_a)
    assert resp_a.status_code == 200
    titles_a = [p["title"] for p in resp_a.json()]
    assert "Produto A" in titles_a
    assert "Produto B" not in titles_a

    # Acesso direto ao produto do tenant B deve dar 404 (via sugestões que valida o produto)
    resp_direct = client.get(f"/products/{isolation_setup['prod_b_id']}/suggestions", headers=headers_a)
    assert resp_direct.status_code == 404

    headers_b = {"Authorization": f"Bearer {isolation_setup['token_b']}"}
    resp_b = client.get("/products", headers=headers_b)
    assert resp_b.status_code == 200
    titles_b = [p["title"] for p in resp_b.json()]
    assert "Produto B" in titles_b
    assert "Produto A" not in titles_b


def test_tenant_isolation_credentials(isolation_setup):
    client = TestClient(app)
    headers_a = {"Authorization": f"Bearer {isolation_setup['token_a']}"}

    resp_a = client.get("/credentials", headers=headers_a)
    assert resp_a.status_code == 200
    labels_a = [c["label"] for c in resp_a.json()]
    assert "Credencial B" not in labels_a

    # Acesso direto à credencial do tenant B com token do A -> 404
    resp_direct = client.get(f"/credentials/{isolation_setup['cred_b_id']}", headers=headers_a)
    assert resp_direct.status_code == 404
