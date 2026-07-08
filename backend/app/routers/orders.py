from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.app.database import get_db, Order, User
from backend.app.schemas import OrderResponse, OrderSyncResponse
from backend.app.services.order_service import sync_all_orders
from backend.app.security.dependencies import get_current_user, get_current_tenant

router = APIRouter(
    prefix="/orders",
    tags=["Orders"]
)

@router.get("/", response_model=List[OrderResponse])
def list_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    marketplace: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Lista todos os pedidos sincronizados com opções de filtro e paginação."""
    query = db.query(Order).filter(Order.tenant_id == tenant_id)
    if marketplace:
        query = query.filter(Order.marketplace == marketplace)
    if status:
        query = query.filter(Order.status == status)
        
    orders = query.order_by(desc(Order.created_at)).offset(skip).limit(limit).all()
    return orders

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Retorna os detalhes de um pedido específico."""
    order = db.query(Order).filter(Order.id == order_id, Order.tenant_id == tenant_id).first()
    if not order:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return order

@router.post("/sync", response_model=OrderSyncResponse)
def force_sync_orders(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Força a sincronização imediata de pedidos de todas as credenciais ativas do tenant."""
    result = sync_all_orders(db, tenant_id)
    return result
