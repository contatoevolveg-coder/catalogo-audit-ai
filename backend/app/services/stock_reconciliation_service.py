import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.app.database import Product, Credential, StockReconciliationLog
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations.bling import find_product_by_sku, get_stock_quantity
from backend.app.services.marketplace_manage_service import update_product

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_stock(tenant_id: int, db: Session):
    """Compara o saldo real do Bling com a quantidade anunciada em cada marketplace.

    Classifica cada produto com erp_sku em: vendendo_fantasma (Bling=0, anúncio>0 —
    corrigido automaticamente), estoque_preso (Bling>0, anúncio inativo),
    divergencia_quantidade (diferença > 20%) ou ok.
    """
    logger.info(f"[StockReconciliation] Iniciando reconciliação para o tenant {tenant_id}")

    bling_cred = db.query(Credential).filter(
        Credential.tenant_id == tenant_id,
        Credential.provider == "bling",
        Credential.status == "valid",
    ).first()

    if not bling_cred:
        return

    try:
        secret = decrypt_secret(bling_cred.encrypted_secret)
        access_token = secret.get("access_token")
    except Exception:
        return

    if not access_token:
        return

    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.erp_sku != None,
        Product.erp_sku != "",
        Product.external_listing_id != None,
    ).all()

    for product in products:
        try:
            # O Bling identifica o saldo pelo ID interno do produto, não pelo SKU —
            # é preciso resolver o SKU -> ID do Bling antes de consultar o estoque
            # (mesmo padrão usado em erp_sync_service.py).
            status_p, res_p = find_product_by_sku(access_token, product.erp_sku)
            if status_p == "not_found":
                bling_quantity = 0
            elif status_p == "success":
                bling_product_id = res_p.get("id")
                if not bling_product_id:
                    continue
                status_s, res_s = get_stock_quantity(access_token, bling_product_id)
                if status_s == "success":
                    bling_quantity = int(res_s)
                elif status_s == "not_found":
                    bling_quantity = 0
                else:
                    continue
            else:
                continue

            marketplace_quantity = product.available_quantity if product.available_quantity is not None else 0

            category = "ok"
            needs_active_correction = False

            if bling_quantity == 0 and marketplace_quantity > 0:
                category = "vendendo_fantasma"
                needs_active_correction = True
            elif bling_quantity > 0 and product.marketplace_status not in ["active", "under_review"]:
                category = "estoque_preso"
            elif bling_quantity > 0 and marketplace_quantity > 0:
                diff = abs(bling_quantity - marketplace_quantity)
                max_val = max(bling_quantity, marketplace_quantity)
                if (diff / max_val) > 0.2:
                    category = "divergencia_quantidade"

            log_entry = StockReconciliationLog(
                tenant_id=tenant_id,
                product_id=product.id,
                bling_quantity=bling_quantity,
                marketplace_quantity=marketplace_quantity,
                category=category,
                checked_at=_utcnow(),
            )
            db.add(log_entry)
            db.commit()

            if needs_active_correction:
                mkt_cred = db.query(Credential).filter(
                    Credential.tenant_id == tenant_id,
                    Credential.provider == product.marketplace,
                    Credential.status == "valid",
                ).first()

                if mkt_cred:
                    try:
                        update_product(
                            product_id=product.id,
                            tenant_id=tenant_id,
                            db=db,
                            changes={"available_quantity": 0},
                            credential_id=mkt_cred.id,
                            sync_to_ml=True,
                        )
                    except Exception as e:
                        logger.error(f"[StockReconciliation] Erro ao corrigir produto {product.id}: {e}")

        except Exception as e:
            logger.error(f"[StockReconciliation] Erro geral no produto {product.id}: {e}")
            db.rollback()
