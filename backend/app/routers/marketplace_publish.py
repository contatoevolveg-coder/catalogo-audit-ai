from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db, MarketplacePublication, User, Credential
from backend.app.schemas import (
    CategorySuggestion, PublishRequest, MarketplacePublicationResponse
)
from backend.app.services.marketplace_publish_service import publish_product_to_ml, publish_product_to_shopee
from backend.app.integrations.mercado_livre import predict_category as ml_predict_category
from backend.app.security.dependencies import get_current_user, get_current_tenant

router = APIRouter(
    prefix="/marketplace-integrations",
    tags=["Marketplace Publish"]
)

@router.get(
    "/{provider}/category-suggestions",
    response_model=List[CategorySuggestion]
)
def get_category_suggestions(
    provider: str,
    title: str,
    current_user: User = Depends(get_current_user)
):
    """Retorna sugestões de categoria no Mercado Livre a partir do título do anúncio."""
    if not title.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O título não pode ser vazio."
        )
    if provider == "mercado_livre":
        return ml_predict_category(title)
    return [] # Shopee não tem predict_category implementado publicamente aqui

@router.post(
    "/products/{product_id}/publish",
    response_model=MarketplacePublicationResponse
)
def publish_product(
    product_id: int,
    data: PublishRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Envia o anúncio com o título e descrição aprovados para o Marketplace de forma síncrona."""
    credential = db.query(Credential).filter(Credential.id == data.credential_id).first()
    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credencial não encontrada.")

    if credential.provider == "mercado_livre":
        pub = publish_product_to_ml(
            product_id=product_id,
            credential_id=data.credential_id,
            category_id=data.category_id,
            db=db,
            tenant_id=tenant_id
        )
    elif credential.provider == "shopee":
        try:
            cat_id = int(data.category_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da Categoria para Shopee deve ser numérico.")
        pub = publish_product_to_shopee(
            product_id=product_id,
            credential_id=data.credential_id,
            category_id=cat_id,
            db=db,
            tenant_id=tenant_id
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provedor não suportado para publicação.")

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
    tenant_id: int = Depends(get_current_tenant)
):
    """Retorna o histórico de tentativas de publicação daquele produto (mais recente primeiro)."""
    return db.query(MarketplacePublication)\
        .filter(MarketplacePublication.product_id == product_id, MarketplacePublication.tenant_id == tenant_id)\
        .order_by(MarketplacePublication.created_at.desc())\
        .all()
