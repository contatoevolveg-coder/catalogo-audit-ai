import os
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Product, ImportBatch, ImportRow, ExternalCallLog, Suggestion

# Configura banco de dados temporário SQLite para os testes do roteador
TEST_DB_PATH = "test_temp.db"
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


def test_imports_unauthorized(client):
    """Garante que endpoints de importação retornam 401 se JWT estiver ausente."""
    response = client.get("/imports", headers={"skip_auth": True})
    assert response.status_code == 401


@pytest.fixture
def mock_httpx_get(monkeypatch):
    """Mocka requisições httpx.get de forma inteligente para testes de imagem."""
    class MockResponse200:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

    class MockResponse404:
        status_code = 404
        headers = {"content-type": "text/html"}

    def mock_get(url, *args, **kwargs):
        # Retorna erro 404 para domínios que contenham 'invalid'
        if "invalid" in str(url):
            return MockResponse404()
        return MockResponse200()
    
    monkeypatch.setattr(httpx, "get", mock_get)

def test_download_template(client):
    """Testa download do template de importação CSV."""
    response = client.get("/imports/template?marketplace=mercado_livre")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "title;category;price" in response.text

def test_full_import_flow_valid_csv(client, mock_httpx_get):
    """Testa o fluxo completo feliz: Upload -> Mapping -> Validate -> Confirm de um CSV válido."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "import_valido.csv")
    assert os.path.exists(fixture_path), "Fixture import_valido.csv não encontrada!"

    # 1. Upload
    with open(fixture_path, "rb") as f:
        response = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "text/csv")},
            data={"marketplace": "mercado_livre"}
        )
    assert response.status_code == 200
    data = response.json()
    batch_id = data["batch_id"]
    assert len(data["detected_columns"]) > 0
    assert len(data["sample_rows"]) == 3
    assert "title" in data["suggested_mapping"].values()

    # 2. Mapping
    mapping = data["suggested_mapping"]
    mapping_response = client.post(f"/imports/{batch_id}/mapping", json={"mapping": mapping})
    assert mapping_response.status_code == 200
    assert mapping_response.json()["status"] == "mapped"

    # 3. Validate
    validate_response = client.post(f"/imports/{batch_id}/validate")
    assert validate_response.status_code == 200
    val_data = validate_response.json()
    assert val_data["total"] == 3
    assert val_data["valid"] == 3
    assert val_data["invalid"] == 0

    # 4. Confirm
    confirm_response = client.post(f"/imports/{batch_id}/confirm")
    assert confirm_response.status_code == 200
    conf_data = confirm_response.json()
    assert conf_data["imported"] == 3
    assert conf_data["skipped_invalid"] == 0
    assert len(conf_data["created_product_ids"]) == 3

    # Verifica se os produtos foram gravados fisicamente na tabela products com status pending
    db = TestingSessionLocal()
    products = db.query(Product).all()
    assert len(products) == 3
    for p in products:
        assert p.status == "pending"
        assert p.marketplace == "mercado_livre"
    db.close()

def test_mapping_missing_required_422(client):
    """Testa se o endpoint de mapeamento retorna 422 se faltar campos obrigatórios."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "import_valido.csv")
    with open(fixture_path, "rb") as f:
        response = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "text/csv")},
            data={"marketplace": "mercado_livre"}
        )
    batch_id = response.json()["batch_id"]

    # Envia mapeamento vazio
    mapping_response = client.post(f"/imports/{batch_id}/mapping", json={"mapping": {}})
    assert mapping_response.status_code == 422
    assert "Falta mapear campos obrigatórios" in mapping_response.json()["detail"]

def test_import_with_errors_csv(client, mock_httpx_get):
    """Testa se linhas inválidas são corretamente bloqueadas e não criam produtos."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "import_com_erros.csv")
    assert os.path.exists(fixture_path)

    # 1. Upload
    with open(fixture_path, "rb") as f:
        response = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "text/csv")},
            data={"marketplace": "mercado_livre"}
        )
    assert response.status_code == 200
    data = response.json()
    batch_id = data["batch_id"]
    mapping = data["suggested_mapping"]

    # 2. Mapping
    client.post(f"/imports/{batch_id}/mapping", json={"mapping": mapping})

    # 3. Validate
    validate_res = client.post(f"/imports/{batch_id}/validate").json()
    # Total de 9 linhas (2 válidas, 7 com erros variados de bloqueio)
    assert validate_res["total"] == 9
    assert validate_res["valid"] == 2      # Linha 1 (limpa) e Linha 9 (warnings apenas)
    assert validate_res["invalid"] == 7    # Linha 2 (título longo), 3 (termo proibido), 4 (preço <= 0), 5 (estoque 0), 6 (condition inválida), 7 (url imagem quebrada), 8 (sem categoria)

    # Verifica se conseguimos filtrar as linhas com erro
    invalid_rows_res = client.get(f"/imports/{batch_id}/rows?status=invalid")
    assert len(invalid_rows_res.json()) == 7

    # Verifica se conseguimos filtrar as linhas com warning
    warning_rows_res = client.get(f"/imports/{batch_id}/rows?status=warning")
    assert len(warning_rows_res.json()) == 1

    # 4. Confirm
    confirm_res = client.post(f"/imports/{batch_id}/confirm").json()
    assert confirm_res["imported"] == 2
    assert confirm_res["skipped_invalid"] == 7

    # Verifica que somente os 2 produtos válidos estão no banco
    db = TestingSessionLocal()
    products = db.query(Product).all()
    assert len(products) == 2
    titles = [p.title for p in products]
    assert "Fone Válido JBL Tune 510" in titles
    assert "Fone com Avisos" in titles
    db.close()

def test_full_import_xlsx(client, mock_httpx_get):
    """Testa se o fluxo funciona idêntico para arquivos Excel (XLSX)."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "import_xlsx_valido.xlsx")
    assert os.path.exists(fixture_path), "Fixture import_xlsx_valido.xlsx não encontrada!"

    # 1. Upload
    with open(fixture_path, "rb") as f:
        response = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"marketplace": "mercado_livre"}
        )
    assert response.status_code == 200
    data = response.json()
    batch_id = data["batch_id"]

    # 2. Mapping
    client.post(f"/imports/{batch_id}/mapping", json={"mapping": data["suggested_mapping"]})

    # 3. Validate
    validate_res = client.post(f"/imports/{batch_id}/validate").json()
    assert validate_res["total"] == 3
    assert validate_res["valid"] == 3

    # 4. Confirm
    confirm_res = client.post(f"/imports/{batch_id}/confirm").json()
    assert confirm_res["imported"] == 3

def test_discard_batch(client):
    """Testa se deletar o batch remove todas as suas dependências do banco de dados (cascade)."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "import_valido.csv")
    with open(fixture_path, "rb") as f:
        response = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "text/csv")},
            data={"marketplace": "mercado_livre"}
        )
    batch_id = response.json()["batch_id"]

    # Deleta batch
    delete_res = client.delete(f"/imports/{batch_id}")
    assert delete_res.status_code == 200

    # Verifica que não sobrou nenhum registro nas tabelas do batch
    db = TestingSessionLocal()
    assert db.query(ImportBatch).filter(ImportBatch.id == batch_id).first() is None
    assert db.query(ImportRow).filter(ImportRow.batch_id == batch_id).all() == []
    db.close()

def _upload_map_validate(client, fixture_name="import_valido.csv"):
    """Helper: sobe a fixture, mapeia e valida, devolvendo o batch_id."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", fixture_name)
    with open(fixture_path, "rb") as f:
        up = client.post(
            "/imports",
            files={"file": (os.path.basename(fixture_path), f, "text/csv")},
            data={"marketplace": "mercado_livre"},
        ).json()
    batch_id = up["batch_id"]
    client.post(f"/imports/{batch_id}/mapping", json={"mapping": up["suggested_mapping"]})
    client.post(f"/imports/{batch_id}/validate")
    return batch_id

def test_confirm_persists_all_fields(client, mock_httpx_get):
    """Garante que estoque, condição e atributos (marca/modelo/gtin) são persistidos no produto."""
    batch_id = _upload_map_validate(client)
    client.post(f"/imports/{batch_id}/confirm")

    db = TestingSessionLocal()
    prod = db.query(Product).filter(Product.title == "Fone de Ouvido Bluetooth JBL Tune 510").first()
    assert prod is not None
    assert prod.available_quantity == 10
    assert prod.condition == "new"
    assert prod.attributes["brand"] == "JBL"
    assert prod.attributes["model"] == "Tune 510BT"
    assert prod.attributes["gtin_ean"] == "7891234567890"
    db.close()

def test_double_confirm_blocked(client, mock_httpx_get):
    """Confirmar o mesmo lote duas vezes deve ser bloqueado (evita produtos duplicados)."""
    batch_id = _upload_map_validate(client)
    first = client.post(f"/imports/{batch_id}/confirm")
    assert first.status_code == 200
    second = client.post(f"/imports/{batch_id}/confirm")
    assert second.status_code == 400

    db = TestingSessionLocal()
    assert db.query(Product).count() == 3  # não duplicou
    db.close()

def test_confirm_import_auto_audit_true(client, mock_httpx_get):
    """Confirma o lote com auto_audit=True e verifica se todos os produtos foram auditados com sucesso."""
    batch_id = _upload_map_validate(client)
    
    response = client.post(f"/imports/{batch_id}/confirm?auto_audit=true")
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 3
    assert data["audited"] == 3
    assert data["audit_skipped"] == 0
    assert data["audit_errors"] == []

    db = TestingSessionLocal()
    # Verifica que todos os 3 produtos foram criados e têm status 'audited'
    products = db.query(Product).all()
    assert len(products) == 3
    for p in products:
        assert p.status == "audited"

    # Verifica que existem 3 sugestões geradas
    suggestions = db.query(Suggestion).all()
    assert len(suggestions) == 3
    db.close()

def test_confirm_import_auto_audit_max_audit_1(client, mock_httpx_get):
    """Confirma o lote com auto_audit=True e max_audit=1, garantindo que o limite seja respeitado."""
    batch_id = _upload_map_validate(client)

    response = client.post(f"/imports/{batch_id}/confirm?auto_audit=true&max_audit=1")
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 3
    assert data["audited"] == 1
    assert data["audit_skipped"] == 2
    assert data["audit_errors"] == []

    db = TestingSessionLocal()
    # Verifica status dos produtos (1 auditado, 2 pendentes)
    audited_prods = db.query(Product).filter(Product.status == "audited").all()
    pending_prods = db.query(Product).filter(Product.status == "pending").all()
    assert len(audited_prods) == 1
    assert len(pending_prods) == 2

    # Verifica que apenas 1 sugestão foi criada no banco
    assert db.query(Suggestion).count() == 1
    db.close()

def test_confirm_import_auto_audit_false_explicit(client, mock_httpx_get):
    """Confirma o lote com auto_audit=False e garante comportamento padrão (produtos pending)."""
    batch_id = _upload_map_validate(client)

    response = client.post(f"/imports/{batch_id}/confirm?auto_audit=false")
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 3
    assert data["audited"] == 0
    assert data["audit_skipped"] == 0
    assert data["audit_errors"] == []

    db = TestingSessionLocal()
    # Todos os produtos devem estar pendentes
    products = db.query(Product).all()
    assert len(products) == 3
    for p in products:
        assert p.status == "pending"

    # Nenhuma sugestão criada
    assert db.query(Suggestion).count() == 0
    db.close()

def test_confirm_import_auto_audit_resilience(client, mock_httpx_get, monkeypatch):
    """Testa resiliência a falhas: se a auditoria de um produto falhar, o lote é confirmado normalmente e os outros são auditados."""
    batch_id = _upload_map_validate(client)

    # Mock perform_product_audit para falhar no segundo produto auditado
    from backend.app.services.import_service import perform_product_audit as original_perform_product_audit
    
    call_count = 0
    def mock_perform(product, db):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("Erro simulado na auditoria do produto")
        return original_perform_product_audit(product, db)

    monkeypatch.setattr("backend.app.services.import_service.perform_product_audit", mock_perform)

    response = client.post(f"/imports/{batch_id}/confirm?auto_audit=true")
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 3
    assert data["audited"] == 2
    assert data["audit_skipped"] == 1
    assert len(data["audit_errors"]) == 1
    assert "Erro simulado" in data["audit_errors"][0]["error"]

    db = TestingSessionLocal()
    # 2 produtos devem ter sido auditados, 1 deve ter continuado pendente
    audited_prods = db.query(Product).filter(Product.status == "audited").all()
    pending_prods = db.query(Product).filter(Product.status == "pending").all()
    assert len(audited_prods) == 2
    assert len(pending_prods) == 1
    
    # 2 sugestões devem existir
    assert db.query(Suggestion).count() == 2
    db.close()

