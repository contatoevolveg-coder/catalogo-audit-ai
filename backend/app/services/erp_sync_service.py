import time
import logging
from datetime import datetime, timezone
from typing import List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import Product, Credential, ErpSyncLog, ExternalCallLog
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations import bling
from backend.app.services.oauth_service import refresh_if_needed

logger = logging.getLogger(__name__)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def sync_product_stock(product_id: int, credential_id: int, db: Session, tenant_id: int) -> ErpSyncLog:
    """Sincroniza o saldo de estoque físico de um produto individual do Bling ERP para a base local.

    Esta operação é estritamente de leitura em relação à API do Bling.
    """
    # 1. Busca o produto
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produto não encontrado."
        )

    # 2. Valida se o SKU está vinculado
    erp_sku = (product.erp_sku or "").strip()
    if not erp_sku:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Produto não possui SKU do Bling vinculado. Use PATCH .../erp-link primeiro."
        )

    # 3. Busca a Credential
    credential = db.query(Credential).filter(Credential.id == credential_id, Credential.tenant_id == tenant_id).first()
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credencial não encontrada."
        )

    if credential.provider != "bling" or credential.status not in ["valid", "expired"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credencial não está válida."
        )

    # 4. Refresh token (OAuth2 Fase 10)
    credential = refresh_if_needed(credential, db)
    if credential.status != "valid":
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na renovação da credencial: {credential.status_detail}"
        )

    # 5. Decripta o secret em memória
    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
        access_token = secret_payload.get("access_token")
        if not access_token:
            raise ValueError("Token de acesso ausente na credencial.")
    except Exception as e:
        logger.error(f"Erro ao decriptografar credencial: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Falha ao decriptografar chave de acesso: {str(e)}"
        )

    # Instancia o log de sincronização preliminar
    sync_log = ErpSyncLog(
        tenant_id=tenant_id,
        product_id=product_id,
        credential_id=credential_id,
        erp_sku=erp_sku,
        sync_type="stock_pull",
        previous_quantity=product.available_quantity,
        created_at=_utcnow()
    )

    start_time = time.time()
    success = False

    # 5. Localiza o produto no Bling por SKU
    status_p, res_p = bling.find_product_by_sku(access_token, erp_sku)
    sync_log.response_payload = res_p

    if status_p == "expired":
        credential.status = "expired"
        credential.status_detail = "Token expirado na sincronização do Bling"
        credential.updated_at = _utcnow()
        sync_log.status = "error"
        sync_log.error_detail = "Token de acesso expirado (HTTP 401/403)."
    elif status_p == "not_found":
        sync_log.status = "not_found"
        sync_log.error_detail = res_p.get("message")
    elif status_p == "error":
        sync_log.status = "error"
        sync_log.error_detail = str(res_p.get("message") or res_p)
    elif status_p == "success":
        # Encontrou o produto. Extrai o ID do Bling para consultar o estoque
        bling_product_id = res_p.get("id")
        if not bling_product_id:
            sync_log.status = "error"
            sync_log.error_detail = "ID do produto ausente no retorno do Bling."
        else:
            # 6. Consulta o estoque no Bling
            status_s, res_s = bling.get_stock_quantity(access_token, bling_product_id)
            if status_s == "expired":
                credential.status = "expired"
                credential.status_detail = "Token expirado na consulta de estoque do Bling"
                credential.updated_at = _utcnow()
                sync_log.status = "error"
                sync_log.error_detail = "Token de acesso expirado na consulta de estoque (HTTP 401/403)."
            elif status_s == "not_found":
                sync_log.status = "not_found"
                sync_log.error_detail = res_s.get("message")
            elif status_s == "error":
                sync_log.status = "error"
                sync_log.error_detail = str(res_s.get("message") or res_s)
            elif status_s == "success":
                # Sucesso: atualiza localmente
                success = True
                new_qty = res_s  # Inteiro retornado do estoque
                sync_log.status = "success"
                sync_log.new_quantity = new_qty
                sync_log.response_payload = {"product_data": res_p, "stock_quantity": new_qty}
                
                # Salva o novo estoque no produto
                product.available_quantity = new_qty

    latency = time.time() - start_time

    # 7. Grava no ExternalCallLog
    db_ext_log = ExternalCallLog(
        kind="bling_stock_sync",
        target_url="https://api.bling.com.br/Api/v3/produtos",
        status_code=200 if success else 400,
        success=success,
        latency_seconds=latency,
        detail={
            "product_id": product_id,
            "credential_id": credential_id,
            "status": sync_log.status,
            "error_detail": sync_log.error_detail
        }
    )

    db.add(sync_log)
    db.add(db_ext_log)
    db.commit()
    db.refresh(sync_log)

    return sync_log


def sync_all_linked_products(credential_id: int, db: Session, tenant_id: int, max_sync: int = 20) -> List[ErpSyncLog]:
    """Itera e sincroniza estoque de todos os produtos vinculados ao Bling de forma resiliente."""
    # Busca os produtos que têm SKU preenchido
    products = db.query(Product)\
        .filter(Product.erp_sku.isnot(None), Product.erp_sku != "", Product.tenant_id == tenant_id)\
        .limit(max_sync)\
        .all()

    logs = []
    for p in products:
        try:
            # Sincronização individual por produto
            log_entry = sync_product_stock(p.id, credential_id, db, tenant_id)
            logs.append(log_entry)
        except Exception as e:
            logger.error(f"Falha de sincronização resiliente para produto {p.id}: {str(e)}")
            err_log = ErpSyncLog(
                tenant_id=tenant_id,
                product_id=p.id,
                credential_id=credential_id,
                erp_sku=p.erp_sku or "",
                sync_type="stock_pull",
                status="error",
                error_detail=str(e),
                created_at=_utcnow()
            )
            db.add(err_log)
            db.commit()
            logs.append(err_log)

    return logs
