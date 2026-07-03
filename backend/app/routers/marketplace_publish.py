from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db, MarketplacePublication, User
from backend.app.schemas import (
    CategorySuggestion, PublishRequest, MarketplacePublicationResponse
)
from backend.app.services.marketplace_publish_service import publish_product_to_ml
from backend.app.integrations.mercado_livre import predict_category
from backend.app.security.dependencies import get_current_user

router = APIRouter(
    prefix="/marketplace-integrations/mercado-livre",
    tags=["Marketplace Publish"]
)

@router.get(
    "/category-suggestions",
    response_model=List[CategorySuggestion]
)
def get_category_suggestions(
    title: str,
    current_user: User = Depends(get_current_user)
):
    """Retorna sugestões de categoria no Mercado Livre a partir do título do anúncio."""
    if not title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O título não pode ser vazio."
        )
    return predict_category(title)

@router.post(
    "/products/{product_id}/publish",
    response_model=MarketplacePublicationResponse
)
def publish_product(
    product_id: int,
    data: PublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Envia o anúncio com o título e descrição aprovados para o Mercado Livre de forma síncrona."""
    pub = publish_product_to_ml(
        product_id=product_id,
        credential_id=data.credential_id,
        category_id=data.category_id,
        db=db
    )
    if pub.status == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=pub.error_detail
        )
    return pub

@router.get(
    "/products/{product_id}/publications",
    response_model=List[MarketplacePublicationResponse]
)
def get_publications_history(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retorna o histórico de tentativas de publicação daquele produto (mais recente primeiro)."""
    return db.query(MarketplacePublication)\
        .filter(MarketplacePublication.product_id == product_id)\
        .order_by(MarketplacePublication.created_at.desc())\
        .all()
