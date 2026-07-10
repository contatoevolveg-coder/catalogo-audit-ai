import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

from backend.app.main import app
from backend.app.database import Base, get_db, Credential, SchedulerRun, CustomerQuestion
from backend.app import config
import backend.app.scheduler

TEST_DB_PATH = "test_temp_scheduler.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    Base.metadata.create_all(bind=engine)
    # Monkeypatch the scheduler SessionLocal to use TestingSessionLocal
    monkeypatch.setattr(backend.app.scheduler, "SessionLocal", TestingSessionLocal)
    
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

@pytest.fixture
def auth_headers(monkeypatch):
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", "u-fQYjE5T4lXf8T3u8-bYh5XvG-Y-u1p6z8Q1vUuM18=")
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    
    with TestClient(app) as base_client:
        base_client.post(
            "/auth/register",
            json={
                "username": "test_admin_user",
                "password": "securepassword123"
            },
            headers={"X-Admin-Key": "test-admin-secret-key"}
        )
        login_res = base_client.post(
            "/auth/login",
            data={
                "username": "test_admin_user",
                "password": "securepassword123"
            }
        )
        token = login_res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

def test_sync_bling_stock_job(monkeypatch):
    db = TestingSessionLocal()
    cred = Credential(
        tenant_id=1,
        provider="bling",
        provider_type="erp",
        label="My Bling",
        encrypted_secret="enc_secret",
        masked_preview="••••1234",
        scopes=["read_products"],
        status="valid"
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    db.close()

    mock_log = MagicMock()
    mock_log.status = "success"
    mock_log.erp_sku = "SKU-123"
    
    with patch("backend.app.scheduler.sync_all_linked_products", return_value=[mock_log]) as mock_sync:
        backend.app.scheduler.sync_bling_stock_job()
        mock_sync.assert_called_once()
        
    db = TestingSessionLocal()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 1
    assert runs[0].job == "sync_bling_stock"
    assert runs[0].items_processed == 1
    assert runs[0].errors is None
    db.close()

def test_sync_ml_questions_job(monkeypatch):
    db = TestingSessionLocal()
    cred = Credential(
        tenant_id=1,
        provider="mercado_livre",
        provider_type="marketplace",
        label="My ML",
        encrypted_secret="enc_secret",
        masked_preview="••••5678",
        scopes=["read_products"],
        status="valid"
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    
    question = CustomerQuestion(
        tenant_id=1,
        credential_id=cred.id,
        ml_question_id="12345",
        item_id="MLB123",
        question_text="Tem estoque?",
        status="pending_draft"
    )
    db.add(question)
    db.commit()
    db.close()

    with patch("backend.app.scheduler.sync_pending_questions", return_value={"synced": 1}) as mock_sync_q, \
         patch("backend.app.scheduler.generate_draft_answer") as mock_draft:
        backend.app.scheduler.sync_ml_questions_job()
        mock_sync_q.assert_called_once()
        mock_draft.assert_called_once()

    db = TestingSessionLocal()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 1
    assert runs[0].job == "sync_ml_questions"
    assert runs[0].items_processed == 1
    assert runs[0].errors is None
    db.close()

def test_refresh_tokens_job(monkeypatch):
    db = TestingSessionLocal()
    cred = Credential(
        tenant_id=1,
        provider="mercado_livre",
        provider_type="marketplace",
        label="My ML Pro",
        encrypted_secret="enc_secret",
        masked_preview="••••9999",
        scopes=["read_products"],
        status="valid"
    )
    db.add(cred)
    db.commit()
    db.close()

    with patch("backend.app.scheduler.refresh_if_needed") as mock_refresh:
        backend.app.scheduler.refresh_tokens_job()
        mock_refresh.assert_called_once()

    db = TestingSessionLocal()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 1
    assert runs[0].job == "refresh_tokens"
    assert runs[0].items_processed == 1
    db.close()

def test_start_and_shutdown_scheduler(monkeypatch):
    monkeypatch.setenv("VERCEL", "")
    with patch("backend.app.scheduler.BackgroundScheduler") as mock_sched_class:
        mock_sched = MagicMock()
        mock_sched_class.return_value = mock_sched
        
        backend.app.scheduler.start_scheduler()
        mock_sched_class.assert_called_once()
        mock_sched.start.assert_called_once()
        
        backend.app.scheduler.shutdown_scheduler()
        mock_sched.shutdown.assert_called_once()

def test_get_scheduler_status_endpoint(auth_headers):
    db = TestingSessionLocal()
    run1 = SchedulerRun(
        job="sync_bling_stock",
        start_time=datetime.fromtimestamp(1000, timezone.utc),
        end_time=datetime.fromtimestamp(1001, timezone.utc),
        items_processed=5,
        errors="Some error"
    )
    run2 = SchedulerRun(
        job="sync_ml_questions",
        start_time=datetime.fromtimestamp(2000, timezone.utc),
        end_time=datetime.fromtimestamp(2001, timezone.utc),
        items_processed=2,
        errors=None
    )
    db.add(run1)
    db.add(run2)
    db.commit()
    db.close()

    with TestClient(app) as client:
        response = client.get("/scheduler/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["job"] == "sync_ml_questions"
        assert data[1]["job"] == "sync_bling_stock"
        assert data[1]["items_processed"] == 5
        assert data[1]["errors"] == "Some error"
