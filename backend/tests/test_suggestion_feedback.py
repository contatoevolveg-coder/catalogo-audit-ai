import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db, Tenant, User, Product, Suggestion, SuggestionFeedback
from backend.app.security.auth import hash_password, create_access_token
from backend.app import config

TEST_DB_PATH = "test_temp_feedback.db"
engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "test-jwt-secret-key-123456789")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "test-admin-secret-key")
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    db.add(Tenant(name="Tenant Teste"))
    db.commit()
    db.add(User(tenant_id=1, username="fb_admin", hashed_password=hash_password("x"), role="admin", is_active=True))
    db.commit()
    db.close()

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
def db_session():
    db = TestingSessionLocal()
    yield db
    db.close()


@pytest.fixture
def test_user_token():
    return create_access_token("fb_admin")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_product():
    return Product(tenant_id=1, title="Produto Fixture", marketplace="mercado_livre", status="pending", price=10.0)


def _seed_suggestion(db, product):
    db.add(product)
    db.commit()
    s = Suggestion(
        tenant_id=1,
        product_id=product.id,
        suggested_title="Title IA",
        suggested_description="Desc IA",
        seo_score=90,
        status="pending",
    )
    db.add(s)
    db.commit()
    return s


def test_approve_suggestion_without_edits(client, db_session, test_product, test_user_token):
    headers = {"Authorization": f"Bearer {test_user_token}"}
    suggestion = _seed_suggestion(db_session, test_product)

    response = client.post(f"/suggestions/{suggestion.id}/approve", json={}, headers=headers)
    assert response.status_code == 200

    db_session.refresh(test_product)
    assert test_product.title == "Title IA"

    feedbacks = db_session.query(SuggestionFeedback).filter_by(suggestion_id=suggestion.id).all()
    assert len(feedbacks) == 2
    title_fb = next(f for f in feedbacks if f.field == "title")
    assert title_fb.was_edited is False
    assert title_fb.edit_distance == 0.0


def test_approve_suggestion_with_edits(client, db_session, test_product, test_user_token):
    headers = {"Authorization": f"Bearer {test_user_token}"}
    suggestion = _seed_suggestion(db_session, test_product)

    response = client.post(
        f"/suggestions/{suggestion.id}/approve",
        json={"final_title": "Title Editado", "final_description": "Desc Editada"},
        headers=headers,
    )
    assert response.status_code == 200

    db_session.refresh(test_product)
    assert test_product.title == "Title Editado"
    assert test_product.description == "Desc Editada"

    feedbacks = db_session.query(SuggestionFeedback).filter_by(suggestion_id=suggestion.id).all()
    assert len(feedbacks) == 2
    title_fb = next(f for f in feedbacks if f.field == "title")
    assert title_fb.was_edited is True
    assert title_fb.edit_distance > 0.0


def test_feedback_stats(client, db_session, test_user_token):
    headers = {"Authorization": f"Bearer {test_user_token}"}
    p = Product(tenant_id=1, title="P1", marketplace="mercado_livre", status="pending")
    db_session.add(p)
    db_session.commit()
    s = Suggestion(tenant_id=1, product_id=p.id, suggested_title="A", suggested_description="B", seo_score=100, status="pending")
    db_session.add(s)
    db_session.commit()

    client.post(f"/suggestions/{s.id}/approve", json={"final_title": "A modified"}, headers=headers)

    response = client.get("/feedback/stats", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_approved"] > 0
    assert data["total_edited"] > 0
    assert data["most_edited_field"] == "title"
