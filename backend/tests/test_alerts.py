import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta

from backend.app.main import app
from backend.app.database import Base, get_db, Product, Credential, Suggestion, CustomerQuestion, Alert
from backend.app import config
from backend.app.security.crypto import encrypt_secret

TEST_DB_PATH = "test_temp_alerts.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
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

def test_alert_low_stock(auth_headers):
    db = TestingSessionLocal()
    # 1 published low stock, 1 pending low stock, 1 published high stock
    p1 = Product(title="Prod A", description="", category="", price=10.0, marketplace="mercado_livre", images=[], status="published", available_quantity=2)
    p2 = Product(title="Prod B", description="", category="", price=10.0, marketplace="mercado_livre", images=[], status="pending", available_quantity=1)
    p3 = Product(title="Prod C", description="", category="", price=10.0, marketplace="mercado_livre", images=[], status="published", available_quantity=10)
    db.add_all([p1, p2, p3])
    db.commit()
    p1_id = p1.id
    db.close()

    with TestClient(app) as client:
        res = client.post("/alerts/trigger", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["created"] == 1
        
        # Calling again should create 0 new alerts because existing is unread
        res = client.post("/alerts/trigger", headers=auth_headers)
        assert res.json()["created"] == 0

        # Verify alert
        alerts_res = client.get("/alerts?is_read=false", headers=auth_headers)
        alerts = alerts_res.json()
        assert len(alerts) == 1
        assert alerts[0]["type"] == "low_stock"
        assert alerts[0]["severity"] == "HIGH"
        assert alerts[0]["product_id"] == p1_id

def test_alert_expiring_credential(auth_headers, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", "u-fQYjE5T4lXf8T3u8-bYh5XvG-Y-u1p6z8Q1vUuM18=")
    db = TestingSessionLocal()
    
    # Credential expiring in 12h, NO refresh token
    enc_no_refresh = encrypt_secret({"access_token": "abc"})
    c1 = Credential(provider="mercado_livre", provider_type="marketplace", label="C1", encrypted_secret=enc_no_refresh, masked_preview="", scopes=[], status="valid", token_expires_at=datetime.utcnow() + timedelta(hours=12))
    
    # Credential expiring in 12h, WITH refresh token
    enc_with_refresh = encrypt_secret({"access_token": "abc", "refresh_token": "xyz"})
    c2 = Credential(provider="mercado_livre", provider_type="marketplace", label="C2", encrypted_secret=enc_with_refresh, masked_preview="", scopes=[], status="valid", token_expires_at=datetime.utcnow() + timedelta(hours=12))
    
    # Credential expiring in 48h, NO refresh token
    c3 = Credential(provider="mercado_livre", provider_type="marketplace", label="C3", encrypted_secret=enc_no_refresh, masked_preview="", scopes=[], status="valid", token_expires_at=datetime.utcnow() + timedelta(hours=48))
    
    db.add_all([c1, c2, c3])
    db.commit()
    c1_id = c1.id
    db.close()

    with TestClient(app) as client:
        client.post("/alerts/trigger", headers=auth_headers)
        alerts = client.get("/alerts?is_read=false", headers=auth_headers).json()
        
        # Only c1 should generate an alert
        assert len(alerts) == 1
        assert alerts[0]["type"] == "credential_expiring"
        assert alerts[0]["credential_id"] == c1_id

def test_alert_low_seo_score(auth_headers):
    db = TestingSessionLocal()
    p = Product(title="Prod SEO", description="", category="", price=10.0, marketplace="mercado_livre", images=[], status="audited")
    db.add(p)
    db.commit()
    p_id = p.id
    
    # Score 40 (should alert)
    s1 = Suggestion(product_id=p.id, suggested_title="", suggested_description="", seo_score=40, missing_attributes=[], image_issues=[], status="pending")
    db.add(s1)
    db.commit()
    db.close()

    with TestClient(app) as client:
        client.post("/alerts/trigger", headers=auth_headers)
        alerts = client.get("/alerts", headers=auth_headers).json()
        
        assert len(alerts) == 1
        assert alerts[0]["type"] == "low_seo_score"
        assert alerts[0]["product_id"] == p_id

def test_alert_answer_pending(auth_headers):
    db = TestingSessionLocal()
    c = Credential(provider="mercado_livre", provider_type="marketplace", label="C1", encrypted_secret="", masked_preview="", scopes=[], status="valid")
    db.add(c)
    db.commit()
    
    # Fetched 3 hours ago
    q1 = CustomerQuestion(credential_id=c.id, ml_question_id="Q1", item_id="1", question_text="Q?", status="pending_draft", fetched_at=datetime.utcnow() - timedelta(hours=3))
    # Fetched 1 hour ago
    q2 = CustomerQuestion(credential_id=c.id, ml_question_id="Q2", item_id="2", question_text="Q2?", status="pending_draft", fetched_at=datetime.utcnow() - timedelta(hours=1))
    
    db.add_all([q1, q2])
    db.commit()
    q1_id = q1.id
    db.close()

    with TestClient(app) as client:
        client.post("/alerts/trigger", headers=auth_headers)
        alerts = client.get("/alerts", headers=auth_headers).json()
        
        # Only q1 should alert
        assert len(alerts) == 1
        assert alerts[0]["type"] == "answer_pending"
        assert str(q1_id) in alerts[0]["message"]

def test_mark_alert_read(auth_headers):
    db = TestingSessionLocal()
    alert = Alert(type="test", severity="LOW", message="Msg", is_read=False)
    db.add(alert)
    db.commit()
    alert_id = alert.id
    db.close()

    with TestClient(app) as client:
        res = client.post(f"/alerts/{alert_id}/read", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["is_read"] == True
        
        alerts = client.get("/alerts?is_read=false", headers=auth_headers).json()
        assert len(alerts) == 0
        
        alerts_all = client.get("/alerts?is_read=true", headers=auth_headers).json()
        assert len(alerts_all) == 1
