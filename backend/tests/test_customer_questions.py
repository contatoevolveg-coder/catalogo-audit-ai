import os
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet

from backend.app.main import app
from backend.app.database import Base, get_db, Product, Credential, CustomerQuestion, ExternalCallLog
from backend.app.schemas import CredentialCreate
from backend.app.services import credential_service
from backend.app import config

TEST_DB_PATH = "test_temp_questions.db"
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
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")

@pytest.fixture
def mock_questions_api(monkeypatch):
    """Mocka o endpoint do Mercado Livre para retornar 3 perguntas."""
    def mock_get(url, *args, **kwargs):
        if "received_questions/search" in url:
            return httpx.Response(
                200,
                json={
                    "questions": [
                        {
                            "id": "1111",
                            "item_id": "MLB001",
                            "text": "Qual a voltagem?",
                            "from": {"nickname": "comprador_A"},
                            "date_created": "2026-07-03T10:00:00.000-03:00"
                        },
                        {
                            "id": "2222",
                            "item_id": "MLB002",
                            "text": "Já comprei o produto, onde está o código de rastreamento?",
                            "from": {"nickname": "comprador_B"},
                            "date_created": "2026-07-03T11:00:00.000-03:00"
                        },
                        {
                            "id": "3333",
                            "item_id": "MLB001",
                            "text": "Tem garantia?",
                            "from": {"nickname": "comprador_C"},
                            "date_created": "2026-07-03T12:00:00.000-03:00"
                        }
                    ]
                }
            )
        return httpx.Response(404)
    monkeypatch.setattr(httpx, "get", mock_get)

def test_questions_endpoints_guard(client):
    """Garante que endpoints sem JWT válido retornam 401."""
    # 1. POST /sync
    res1 = client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": 1, "max_fetch": 5},
        headers={"skip_auth": True}
    )
    assert res1.status_code == 401

    # 2. GET /
    res2 = client.get(
        "/marketplace-integrations/mercado-livre/questions/",
        headers={"skip_auth": True}
    )
    assert res2.status_code == 401

    # 3. GET /1
    res3 = client.get(
        "/marketplace-integrations/mercado-livre/questions/1",
        headers={"skip_auth": True}
    )
    assert res3.status_code == 401

    # 4. POST /1/draft
    res4 = client.post(
        "/marketplace-integrations/mercado-livre/questions/1/draft",
        headers={"skip_auth": True}
    )
    assert res4.status_code == 401

    # 5. POST /1/send
    res5 = client.post(
        "/marketplace-integrations/mercado-livre/questions/1/send",
        json={"credential_id": 1, "final_text": "Ok"},
        headers={"skip_auth": True}
    )
    assert res5.status_code == 401

    # 6. POST /1/dismiss
    res6 = client.post(
        "/marketplace-integrations/mercado-livre/questions/1/dismiss",
        headers={"skip_auth": True}
    )
    assert res6.status_code == 401

def test_sync_questions_success(client, mock_questions_api):
    """Testa sincronização de perguntas com persistência correta e sem duplicar."""
    db = TestingSessionLocal()
    
    # Cria a credencial necessária
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste",
        secret_payload={"access_token": "ACCESS-TOKEN-ML-1234"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    
    # Cria produto correspondente ao MLB001
    prod = Product(
        tenant_id=1,
        title="Celular de Teste",
        description="Celular 128GB.",
        price=1200.0,
        marketplace="mercado_livre",
        status="pending",
        external_listing_id="MLB001"
    )
    db.add(prod)
    db.commit()
    cred_id = cred.id
    db.close()

    # Primeira sincronização
    res1 = client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 10}
    )
    assert res1.status_code == 200
    assert res1.json()["synced"] == 3
    assert res1.json()["skipped_existing"] == 0

    # Verifica persistência no banco
    db2 = TestingSessionLocal()
    questions = db2.query(CustomerQuestion).all()
    assert len(questions) == 3
    
    # Verifica que matched_product_id foi vinculado para a pergunta com MLB001
    q1 = db2.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "1111").first()
    assert q1.matched_product_id is not None
    assert q1.question_text == "Qual a voltagem?"
    assert q1.asker_nickname == "comprador_A"
    
    # Segunda sincronização (deve ignorar já existentes)
    res2 = client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 10}
    )
    assert res2.status_code == 200
    assert res2.json()["synced"] == 0
    assert res2.json()["skipped_existing"] == 3
    db2.close()

def test_sync_questions_respects_max_fetch(client, mock_questions_api):
    """Testa se a sincronização respeita o parâmetro max_fetch."""
    db = TestingSessionLocal()
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste",
        secret_payload={"access_token": "ACCESS-TOKEN-ML-1234"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    cred_id = cred.id
    db.close()

    # Sincronização limitando max_fetch = 2
    res = client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 2}
    )
    assert res.status_code == 200
    assert res.json()["synced"] == 2

    db2 = TestingSessionLocal()
    assert db2.query(CustomerQuestion).count() == 2
    db2.close()

def test_draft_answer_generation(client, mock_questions_api):
    """Testa a geração de rascunhos de resposta pela IA (Gemini)."""
    db = TestingSessionLocal()
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste",
        secret_payload={"access_token": "TOKEN-ML"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    cred_id = cred.id
    db.close()

    # Sincroniza perguntas
    client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 10}
    )

    db2 = TestingSessionLocal()
    q_normal = db2.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "1111").first()
    q_pos_venda = db2.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "2222").first()
    q_normal_id = q_normal.id
    q_pos_venda_id = q_pos_venda.id
    db2.close()

    # 1. Roda draft para pergunta normal
    res1 = client.post(f"/marketplace-integrations/mercado-livre/questions/{q_normal_id}/draft")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "draft_ready"
    assert "especificações" in data1["ai_suggested_answer"]
    assert data1["needs_human_review"] is False

    # 2. Roda draft para pergunta de pós-venda (simula fora de escopo)
    res2 = client.post(f"/marketplace-integrations/mercado-livre/questions/{q_pos_venda_id}/draft")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "draft_ready"
    assert data2["needs_human_review"] is True
    assert "pós-venda" in data2["review_reason"].lower()

def test_send_answer_errors_and_success(client, mock_questions_api, monkeypatch):
    """Testa envio de resposta nas condições de sucesso, erro 400 e erro 401."""
    db = TestingSessionLocal()
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste",
        secret_payload={"access_token": "MY-SECRET-ML-TOKEN"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    cred_id = cred.id
    db.close()

    # Sincroniza perguntas
    client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 10}
    )

    db2 = TestingSessionLocal()
    q = db2.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "1111").first()
    q_id = q.id
    db2.close()

    # 1. Tentar enviar sem rascunho nem final_text -> 400
    res_err = client.post(
        f"/marketplace-integrations/mercado-livre/questions/{q_id}/send",
        json={"credential_id": cred_id}
    )
    assert res_err.status_code == 400
    assert "rascunho" in res_err.json()["detail"].lower()

    # Mock de sucesso no envio ao ML
    monkeypatch.setattr(
        httpx, "post",
        lambda *args, **kwargs: httpx.Response(201, json={"status": "ANSWERED"})
    )

    # 2. Enviar com final_text customizado -> Sucesso
    res_ok = client.post(
        f"/marketplace-integrations/mercado-livre/questions/{q_id}/send",
        json={"credential_id": cred_id, "final_text": "Custom response text"}
    )
    assert res_ok.status_code == 200
    data_ok = res_ok.json()
    assert data_ok["status"] == "approved_sent"
    assert data_ok["final_answer_text"] == "Custom response text"
    assert "MY-SECRET-ML-TOKEN" not in res_ok.text

    # Verifica Log gravado
    db3 = TestingSessionLocal()
    logs = db3.query(ExternalCallLog).filter(ExternalCallLog.kind == "ml_answer_submit").all()
    assert len(logs) == 1
    assert logs[0].success is True
    
    # 3. Enviar com 401 do ML -> Credencial expira
    monkeypatch.setattr(
        httpx, "post",
        lambda *args, **kwargs: httpx.Response(401, json={"error": "unauthorized", "message": "invalid_token"})
    )
    
    # Outra pergunta
    q2 = db3.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "3333").first()
    q2_id = q2.id
    db3.close()

    res_401 = client.post(
        f"/marketplace-integrations/mercado-livre/questions/{q2_id}/send",
        json={"credential_id": cred_id, "final_text": "Correct answer but expired"}
    )
    assert res_401.status_code == 200
    assert res_401.json()["status"] == "error"

    db4 = TestingSessionLocal()
    updated_cred = db4.query(Credential).filter(Credential.id == cred_id).first()
    assert updated_cred.status == "expired"
    db4.close()

def test_dismiss_question(client, mock_questions_api, monkeypatch):
    """Testa o descarte local (dismiss) sem gerar chamadas de rede."""
    db = TestingSessionLocal()
    cred = credential_service.create_credential(db, CredentialCreate(
        provider="mercado_livre",
        provider_type="marketplace",
        label="ML Teste",
        secret_payload={"access_token": "TOKEN"},
        scopes=["read_products"]
    ), tenant_id=1)
    cred.status = "valid"
    db.commit()
    cred_id = cred.id
    db.close()

    # Sincroniza
    client.post(
        "/marketplace-integrations/mercado-livre/questions/sync",
        json={"credential_id": cred_id, "max_fetch": 10}
    )

    db2 = TestingSessionLocal()
    q = db2.query(CustomerQuestion).filter(CustomerQuestion.ml_question_id == "1111").first()
    q_id = q.id
    db2.close()

    # Mocka post para ter certeza de que NENHUMA chamada é feita
    called = False
    def mock_post(*args, **kwargs):
        nonlocal called
        called = True
        return httpx.Response(200)
    monkeypatch.setattr(httpx, "post", mock_post)

    # Dispara dismiss
    res = client.post(f"/marketplace-integrations/mercado-livre/questions/{q_id}/dismiss")
    assert res.status_code == 200
    assert res.json()["status"] == "dismissed"
    assert called is False
