from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db, Product, ErpSyncLog, User
from backend.app.schemas import (
    ErpLinkRequest, SyncStockRequest, BulkSyncRequest,
    ErpSyncLogResponse, BulkSyncResponse, ProductResponse
)
from backend.app.services.erp_sync_service import sync_product_stock, sync_all_linked_products
from backend.app.security.dependencies import get_current_user, get_current_tenant

router = APIRouter(
    prefix="/erp-integrations/bling",
    tags=["ERP Bling"]
)

@router.patch(
    "/products/{product_id}/erp-link",
    response_model=ProductResponse
)
def link_erp_sku(
    product_id: int,
    data: ErpLinkRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Vincula ou atualiza o SKU (código) do Bling ERP associado a um produto local."""
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produto não encontrado."
        )

    product.erp_sku = data.erp_sku
    db.commit()
    db.refresh(product)
    return product


@router.post(
    "/products/{product_id}/sync-stock",
    response_model=ErpSyncLogResponse
)
def sync_stock(
    product_id: int,
    data: SyncStockRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Sincroniza o saldo de estoque físico do produto a partir do Bling ERP (somente leitura)."""
    log_entry = sync_product_stock(
        product_id=product_id,
        credential_id=data.credential_id,
        db=db,
        tenant_id=tenant_id
    )

    if log_entry.status == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=log_entry.error_detail
        )
    elif log_entry.status == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=log_entry.error_detail
        )

    return log_entry


@router.post(
    "/sync-all",
    response_model=BulkSyncResponse
)
def sync_all(
    data: BulkSyncRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Executa a sincronização de estoque em lote para todos os produtos vinculados (com cap de lote)."""
    logs = sync_all_linked_products(
        credential_id=data.credential_id,
        db=db,
        tenant_id=tenant_id,
        max_sync=data.max_sync
    )

    synced_count = 0
    not_found_count = 0
    errors_list = []

    for item in logs:
        if item.status == "success":
            synced_count += 1
        elif item.status == "not_found":
            not_found_count += 1
        elif item.status == "error":
            errors_list.append({
                "product_id": item.product_id,
                "error": item.error_detail
            })

    return BulkSyncResponse(
        synced=synced_count,
        not_found=not_found_count,
        errors=errors_list
    )


@router.get(
    "/products/{product_id}/sync-history",
    response_model=List[ErpSyncLogResponse]
)
def get_sync_history(
    product_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Retorna o histórico de tentativas de sincronização do produto (mais recente primeiro)."""
    return db.query(ErpSyncLog)\
        .filter(ErpSyncLog.product_id == product_id, ErpSyncLog.tenant_id == tenant_id)\
        .order_by(ErpSyncLog.created_at.desc())\
        .all()
