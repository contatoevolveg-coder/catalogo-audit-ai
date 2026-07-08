import pytest
from unittest.mock import patch
from backend.app.database import Suggestion, Product, SuggestionFeedback
from backend.app.constants import ProductStatus, SuggestionStatus

def test_approve_suggestion_without_edits(client, db_session, test_product, test_user_token):
    # Setup
    headers = {"Authorization": f"Bearer {test_user_token}"}
    db_session.add(test_product)
    db_session.commit()
    
    suggestion = Suggestion(
        product_id=test_product.id,
        suggested_title="Title IA",
        suggested_description="Desc IA",
        seo_score=90,
        status="pending"
    )
    db_session.add(suggestion)
    db_session.commit()

    # Approving without edits
    response = client.post(
        f"/suggestions/{suggestion.id}/approve",
        json={}, # No edits
        headers=headers
    )

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
    db_session.add(test_product)
    db_session.commit()
    
    suggestion = Suggestion(
        product_id=test_product.id,
        suggested_title="Title IA",
        suggested_description="Desc IA",
        seo_score=90,
        status="pending"
    )
    db_session.add(suggestion)
    db_session.commit()

    # Approving with edits
    response = client.post(
        f"/suggestions/{suggestion.id}/approve",
        json={
            "final_title": "Title Editado",
            "final_description": "Desc Editada"
        },
        headers=headers
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
    
    # Needs a product and a suggestion first
    from backend.app.database import Product, Suggestion
    p = Product(title="P1", marketplace="mercado_livre")
    db_session.add(p)
    db_session.commit()
    
    s = Suggestion(product_id=p.id, suggested_title="A", suggested_description="B", seo_score=100)
    db_session.add(s)
    db_session.commit()

    client.post(
        f"/suggestions/{s.id}/approve",
        json={"final_title": "A modified"},
        headers=headers
    )

    response = client.get("/feedback/stats", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_approved"] > 0
    assert data["total_edited"] > 0
    assert data["most_edited_field"] == "title"
