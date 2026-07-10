import logging
from typing import Tuple, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import dateutil.parser

from backend.app.database import Order, OrderItem, Product, Credential
from backend.app.services.credential_service import decrypt_secret
from backend.app.integrations.mercado_livre import get_seller_id, fetch_orders

logger = logging.getLogger(__name__)

def sync_ml_orders_for_credential(db: Session, credential: Credential, tenant_id: int) -> Tuple[int, int]:
    """Sincroniza pedidos do Mercado Livre para a base local e abate estoque se pago."""
    if credential.provider != "mercado_livre" or credential.status != "valid":
        return 0, 0

    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
        access_token = secret_payload.get("access_token")
        if not access_token:
            logger.error(f"Credencial {credential.id} não possui access_token.")
            return 0, 0

        seller_id = get_seller_id(access_token)
        if not seller_id:
            logger.error(f"Falha ao obter seller_id para credencial {credential.id}.")
            return 0, 0

        orders = fetch_orders(access_token, seller_id, limit=50)
        
        synced = 0
        stock_deductions = 0

        for ml_order in orders:
            external_order_id = str(ml_order.get("id"))
            status = ml_order.get("status")
            shipping_status = ml_order.get("shipping", {}).get("status")
            total_amount = ml_order.get("total_amount", 0.0)
            buyer_nickname = ml_order.get("buyer", {}).get("nickname")
            
            try:
                created_at_str = ml_order.get("date_created")
                created_at = dateutil.parser.isoparse(created_at_str) if created_at_str else datetime.now(timezone.utc)
            except Exception:
                created_at = datetime.now(timezone.utc)

            # Check if order already exists
            db_order = db.query(Order).filter(Order.external_order_id == external_order_id, Order.tenant_id == tenant_id).first()

            is_new = False
            if not db_order:
                db_order = Order(
                    tenant_id=tenant_id,
                    marketplace="mercado_livre",
                    external_order_id=external_order_id,
                    credential_id=credential.id,
                    buyer_nickname=buyer_nickname,
                    total_amount=total_amount,
                    status=status,
                    shipping_status=shipping_status,
                    stock_deducted=False,
                    created_at=created_at.replace(tzinfo=None)
                )
                db.add(db_order)
                db.flush() # To get db_order.id
                is_new = True
                new_items = []  # itens recém-criados: a sessão tem autoflush=False, então uma
                                 # query por OrderItem.order_id logo em seguida não os enxergaria.

                # Insert Items
                order_items_data = ml_order.get("order_items", [])
                for item_data in order_items_data:
                    item_info = item_data.get("item", {})
                    external_id = item_info.get("id")
                    sku = item_info.get("seller_sku")
                    title = item_info.get("title", "Desconhecido")
                    quantity = item_data.get("quantity", 1)
                    unit_price = item_data.get("unit_price", 0.0)

                    # Tenta dar match no Produto local
                    matched_product = None
                    if external_id:
                        matched_product = db.query(Product).filter(Product.external_listing_id == external_id, Product.tenant_id == tenant_id).first()
                    if not matched_product and sku:
                        matched_product = db.query(Product).filter(Product.erp_sku == sku, Product.tenant_id == tenant_id).first()
                    
                    db_item = OrderItem(
                        order_id=db_order.id,
                        product_id=matched_product.id if matched_product else None,
                        sku=sku,
                        title=title,
                        quantity=quantity,
                        unit_price=unit_price
                    )
                    db.add(db_item)
                    new_items.append(db_item)
            else:
                # Update existing order
                db_order.status = status
                db_order.shipping_status = shipping_status

            # Deduce stock if paid/confirmed and not already deducted
            if db_order.status in ["paid", "confirmed"] and not db_order.stock_deducted:
                items = new_items if is_new else db_order.items
                deducted_any = False
                for item in items:
                    if item.product_id:
                        product = db.query(Product).filter(Product.id == item.product_id, Product.tenant_id == tenant_id).first()
                        if product and product.available_quantity is not None:
                            product.available_quantity = max(0, product.available_quantity - item.quantity)
                            deducted_any = True
                
                # Só marca como deduzido quando houve baixa efetiva. Caso o produto
                # correspondente ainda não exista localmente (item.product_id None),
                # mantém stock_deducted=False para permitir baixa retroativa após a
                # importação do produto (evita perder a dedução).
                if deducted_any:
                    stock_deductions += 1
                    db_order.stock_deducted = True

            synced += 1

        db.commit()
        return synced, stock_deductions

    except Exception as e:
        logger.error(f"Erro ao sincronizar pedidos da credencial {credential.id}: {e}")
        db.rollback()
        return 0, 0

def sync_all_orders(db: Session, tenant_id: int) -> Dict[str, Any]:
    """Sincroniza pedidos de todas as credenciais ativas do Mercado Livre para o tenant."""
    credentials = db.query(Credential).filter(
        Credential.provider == "mercado_livre",
        Credential.status == "valid",
        Credential.tenant_id == tenant_id
    ).all()

    total_synced = 0
    total_deductions = 0
    
    for cred in credentials:
        s, d = sync_ml_orders_for_credential(db, cred, tenant_id)
        total_synced += s
        total_deductions += d

    return {
        "synced": total_synced,
        "stock_deductions": total_deductions
    }
