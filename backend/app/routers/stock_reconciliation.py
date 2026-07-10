from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from backend.app.database import get_db, StockReconciliationLog
from backend.app.security.dependencies import get_current_tenant
from backend.app.services.stock_reconciliation_service import reconcile_stock

router = APIRouter(prefix="/stock-reconciliation", tags=["Reconciliação de Estoque"])

@router.get("/")
def list_reconciliation_logs(
    limit: int = 100,
    tenant_id: int = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    """Lista as últimas discrepâncias de estoque encontradas."""
    logs = db.query(StockReconciliationLog).filter(
        StockReconciliationLog.tenant_id == tenant_id
    ).order_by(StockReconciliationLog.checked_at.desc()).limit(limit).all()

    return [
        {
            "id": log.id,
            "product_id": log.product_id,
            "product_title": log.product.title if log.product else "Desconhecido",
            "erp_sku": log.product.erp_sku if log.product else "N/A",
            "marketplace": log.product.marketplace if log.product else "N/A",
            "category": log.category,
            "bling_quantity": log.bling_quantity,
            "marketplace_quantity": log.marketplace_quantity,
            "checked_at": log.checked_at.isoformat()
        } for log in logs
    ]

@router.post("/run")
def run_reconciliation(
    tenant_id: int = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    """Dispara manualmente a auditoria de reconciliação para o tenant atual."""
    try:
        reconcile_stock(tenant_id, db)
        return {"message": "Reconciliação de estoque executada com sucesso."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao executar reconciliação: {str(e)}"
        )
