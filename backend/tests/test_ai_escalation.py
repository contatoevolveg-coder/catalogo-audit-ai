import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

from backend.app.database import Base, CustomerQuestion, Credential, Alert, Product
from backend.app.services.question_service import generate_draft_answer, send_answer

# Self-contained SQLite for this test
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_generate_draft_exception_escalates():
    db = TestingSessionLocal()

    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", status="valid", label="ML", encrypted_secret="test", masked_preview="test", scopes={})
    db.add(cred)
    db.commit()
    db.refresh(cred)

    # Criar mock data
    q = CustomerQuestion(tenant_id=1, credential_id=cred.id, ml_question_id="Q100", question_text="Funciona?", status="pending_draft", item_id="MLB1")
    db.add(q)
    db.commit()
    db.refresh(q)

    # Patch the agent call to throw exception
    with patch("backend.app.services.question_service.draft_question_answer", side_effect=Exception("AI API DOWN")):
        generate_draft_answer(q.id, db, 1)

    db.refresh(q)
    assert q.status == "error"
    assert "Falha ao gerar rascunho" in q.review_reason

    # Verify Alert was created
    alerts = db.query(Alert).filter_by(tenant_id=1, type="ai_answer_failed").all()
    assert len(alerts) == 1
    assert alerts[0].severity == "HIGH"
    assert "IA não conseguiu gerar rascunho" in alerts[0].message
    assert str(q.id) in alerts[0].message

    # Second call should not duplicate alert (dedup logic)
    with patch("backend.app.services.question_service.draft_question_answer", side_effect=Exception("AI API DOWN")):
        generate_draft_answer(q.id, db, 1)

    alerts = db.query(Alert).filter_by(tenant_id=1, type="ai_answer_failed").all()
    assert len(alerts) == 1  # Still 1!
    db.close()

def test_generate_draft_low_confidence_escalates():
    db = TestingSessionLocal()

    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", status="valid", label="ML", encrypted_secret="test", masked_preview="test", scopes={})
    db.add(cred)
    db.commit()
    db.refresh(cred)

    q = CustomerQuestion(tenant_id=1, credential_id=cred.id, ml_question_id="Q101", question_text="produto veio com defeito", status="pending_draft", item_id="MLB1")
    db.add(q)
    db.commit()
    db.refresh(q)

    # Patch agent to return needs_human_review=True
    with patch("backend.app.services.question_service.draft_question_answer", return_value=("Rascunho", True, "Pós-venda", 10, 10, 0.5)):
        generate_draft_answer(q.id, db, 1)

    db.refresh(q)
    assert q.status == "draft_ready"  # Keeps draft_ready, but escalated
    assert q.needs_human_review is True

    alerts = db.query(Alert).filter_by(tenant_id=1, type="ai_answer_failed").all()
    assert len(alerts) == 1
    assert "IA não teve confiança na resposta" in alerts[0].message
    db.close()

def test_send_answer_failure_escalates():
    db = TestingSessionLocal()

    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", status="valid", label="ML", encrypted_secret="test", masked_preview="test", scopes={})
    db.add(cred)
    db.commit()
    db.refresh(cred)

    q = CustomerQuestion(tenant_id=1, credential_id=cred.id, question_text="Tem?", status="draft_ready", item_id="MLB1", ai_suggested_answer="Sim", ml_question_id="Q1")
    db.add(q)
    db.commit()
    db.refresh(q)

    # Patch external calls: decrypt token ok, but submit fails
    with patch("backend.app.services.question_service.decrypt_secret", return_value={"access_token": "token"}), \
         patch("backend.app.services.question_service.refresh_if_needed", return_value=cred), \
         patch("backend.app.services.question_service.submit_answer", return_value=(False, {"error": "internal_error", "message": "ML DOWN"})):

        send_answer(q.id, cred.id, db, 1)

    db.refresh(q)
    assert q.status == "error"
    assert "internal_error" in q.review_reason

    alerts = db.query(Alert).filter_by(tenant_id=1, type="ai_answer_failed").all()
    assert len(alerts) == 1
    assert "Falha ao enviar a resposta ao Mercado Livre" in alerts[0].message
    db.close()

def test_send_answer_401_no_escalation():
    db = TestingSessionLocal()

    cred = Credential(tenant_id=1, provider="mercado_livre", provider_type="marketplace", status="valid", label="ML", encrypted_secret="test", masked_preview="test", scopes={})
    db.add(cred)
    db.commit()
    db.refresh(cred)

    q = CustomerQuestion(tenant_id=1, credential_id=cred.id, question_text="Tem?", status="draft_ready", item_id="MLB1", ai_suggested_answer="Sim", ml_question_id="Q1")
    db.add(q)
    db.commit()
    db.refresh(q)

    # Patch external calls: decrypt token ok, submit fails with 401
    with patch("backend.app.services.question_service.decrypt_secret", return_value={"access_token": "token"}), \
         patch("backend.app.services.question_service.refresh_if_needed", return_value=cred), \
         patch("backend.app.services.question_service.submit_answer", return_value=(False, {"error": "unauthorized", "message": "401 unauthorized"})):

        send_answer(q.id, cred.id, db, 1)

    db.refresh(q)
    db.refresh(cred)

    assert q.status == "error"
    assert cred.status == "expired"

    # NO alert should be created for 401
    alerts = db.query(Alert).filter_by(tenant_id=1, type="ai_answer_failed").all()
    assert len(alerts) == 0
    db.close()
