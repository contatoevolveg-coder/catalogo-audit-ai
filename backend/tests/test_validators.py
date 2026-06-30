import pytest
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.database import Base, ExternalCallLog
from backend.app.validators import validate_row_ml, validate_image_url

@pytest.fixture
def db_session():
    """Cria uma sessão de banco de dados SQLite em memória isolada para cada teste."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_validate_image_url_success(db_session, monkeypatch):
    """Testa se uma URL de imagem válida e acessível é confirmada com sucesso."""
    class MockResponse:
        status_code = 200
        headers = {"content-type": "image/png"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse())

    success, reason = validate_image_url("https://example.com/imagem_correta.png", db_session)
    assert success is True
    assert reason == ""

    # Verifica se salvou o log correspondente no banco
    log = db_session.query(ExternalCallLog).first()
    assert log is not None
    assert log.success is True
    assert log.status_code == 200
    assert log.kind == "image_check"

def test_validate_image_url_failure(db_session, monkeypatch):
    """Testa se URLs de imagem inacessíveis ou de formato errado retornam erro."""
    class MockResponse404:
        status_code = 404
        headers = {"content-type": "text/html"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse404())

    success, reason = validate_image_url("https://example.com/imagem_inexistente.png", db_session)
    assert success is False
    assert "status 404" in reason

    # Testa content-type inválido
    class MockResponseHTML:
        status_code = 200
        headers = {"content-type": "text/html"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponseHTML())

    success, reason = validate_image_url("https://example.com/fake_image.png", db_session)
    assert success is False
    assert "Content-Type inválido" in reason

def test_validate_row_ml_valid(db_session, monkeypatch):
    """Testa a validação de uma linha 100% correta contendo todos os dados canônicos."""
    class MockResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse())

    row = {
        "title": "Fone de Ouvido Bluetooth JBL Tune 510BT Preto",
        "category": "Eletrônicos > Áudio",
        "price": 199.90,
        "available_quantity": 15,
        "condition": "new",
        "images": "https://example.com/foto.jpg",
        "brand": "JBL",
        "model": "Tune 510BT",
        "description": "Fone de ouvido original com excelente resposta de graves e som Pure Bass.",
        "gtin_ean": "7891234567890"
    }

    errors = validate_row_ml(row, db_session)
    assert len(errors) == 0

def test_validate_row_ml_errors(db_session, monkeypatch):
    """Testa se a validação captura erros de negócio do Mercado Livre e avisos de melhoria."""
    class MockResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: MockResponse())

    # Título longo + Termo promocional + Preço zero + Sem estoque + Sem marca/modelo
    row = {
        "title": "FONE DE OUVIDO SUPER PROMOÇÃO FRETE GRÁTIS COM DESCONTO IMPERDÍVEL HOJE",
        "category": "Áudio",
        "price": 0.0,
        "available_quantity": 0,
        "condition": "weird",
        "images": "https://example.com/foto.jpg",
        "brand": "",
        "model": "",
        "description": "Curta",
        "gtin_ean": "123"  # gtin inválido
    }

    errors = validate_row_ml(row, db_session)
    err_fields = [e["field"] for e in errors]

    # Validações de erro críticas
    assert "title" in err_fields
    assert any(e["field"] == "title" and "caracteres" in e["message"] for e in errors)
    assert any(e["field"] == "title" and "termos promocionais" in e["message"] for e in errors)
    assert "price" in err_fields
    assert "available_quantity" in err_fields
    assert "condition" in err_fields
    assert "gtin_ean" in err_fields

    # Validações de aviso (warnings)
    assert "brand" in err_fields
    assert "model" in err_fields
    assert "description" in err_fields
    
    # Assevera que severidades batem
    assert any(e["field"] == "title" and e["severity"] == "error" for e in errors)
    assert any(e["field"] == "brand" and e["severity"] == "warning" for e in errors)
