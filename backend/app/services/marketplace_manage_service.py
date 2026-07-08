import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import Product, Credential, ExternalCallLog
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations.mercado_livre import (
    update_item, update_item_description, close_item, delete_item
)
from backend.app.services.oauth_service import refresh_if_needed

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_valid_ml_token(credential_id: int, tenant_id: int, db: Session) -> str:
    """Recupera e valida um access_token do Mercado Livre a partir do cofre."""
    credential = db.query(Credential).filter(
        Credential.id == credential_id, Credential.tenant_id == tenant_id
    ).first()
    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credencial não encontrada.")
    if credential.provider != "mercado_livre" or credential.status not in ["valid", "expired"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credencial do Mercado Livre não está válida.")

    credential = refresh_if_needed(credential, db)
    if credential.status != "valid":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na renovação da credencial: {credential.status_detail}"
        )

    secret_payload = decrypt_secret(credential.encrypted_secret)
    access_token = secret_payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token de acesso ausente na credencial.")
    return access_token


def update_product(
    product_id: int,
    tenant_id: int,
    db: Session,
    changes: Dict[str, Any],
    credential_id: Optional[int] = None,
    sync_to_ml: bool = False,
) -> Product:
    """Edita um produto local e, opcionalmente, sincroniza a alteração com o anúncio no ML.

    `changes` aceita: title, description, price, available_quantity, category,
    condition, images (lista), attributes (dict), status.
    """
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")

    # 1. Aplica alterações locais (apenas campos fornecidos)
    editable = {"title", "description", "price", "available_quantity", "category", "condition", "images", "attributes"}
    applied = {}
    for field, value in changes.items():
        if field in editable and value is not None:
            setattr(product, field, value)
            applied[field] = value

    ml_result = None
    # 2. Sincroniza com o Mercado Livre se solicitado e o produto já estiver publicado lá
    if sync_to_ml and product.external_listing_id and credential_id:
        access_token = _get_valid_ml_token(credential_id, tenant_id, db)
        item_id = product.external_listing_id

        ml_changes = {}
        if "title" in applied:
            ml_changes["title"] = str(applied["title"])[:60]
        if "price" in applied:
            ml_changes["price"] = applied["price"]
        if "available_quantity" in applied and applied["available_quantity"] is not None:
            ml_changes["available_quantity"] = int(applied["available_quantity"])
        if "images" in applied and isinstance(applied["images"], list):
            ml_changes["pictures"] = [{"source": u} for u in applied["images"] if u][:10]

        start_time = time.time()
        ok = True
        detail = {}
        if ml_changes:
            ok, detail = update_item(access_token, item_id, ml_changes)
        # Descrição é atualizada em endpoint separado
        if "description" in applied and applied["description"] is not None:
            desc_ok, desc_detail = update_item_description(access_token, item_id, str(applied["description"]))
            ok = ok and desc_ok
            detail = {"item": detail, "description": desc_detail}
        latency = time.time() - start_time

        ml_result = {"success": ok, "detail": detail}

        db_log = ExternalCallLog(
            kind="ml_item_update",
            target_url=f"https://api.mercadolibre.com/items/{item_id}",
            status_code=200 if ok else 400,
            success=ok,
            latency_seconds=latency,
            detail={"product_id": product_id, "credential_id": credential_id, "changes": list(ml_changes.keys())},
        )
        db.add(db_log)

        if not ok:
            db.commit()
            db.refresh(product)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Produto atualizado localmente, mas falhou ao sincronizar com o Mercado Livre: {detail}"
            )

    db.commit()
    db.refresh(product)
    return product


def delete_product(
    product_id: int,
    tenant_id: int,
    db: Session,
    credential_id: Optional[int] = None,
    close_on_marketplace: bool = True,
) -> Dict[str, Any]:
    """Exclui um produto. Se estiver publicado no ML e houver credencial, encerra/deleta o anúncio lá.

    A verificação de permissão (papel do usuário) é feita na camada de rota.
    """
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")

    ml_closed = False
    ml_detail = None
    if close_on_marketplace and product.external_listing_id and credential_id:
        try:
            access_token = _get_valid_ml_token(credential_id, tenant_id, db)
            item_id = product.external_listing_id
            start_time = time.time()
            ok, detail = delete_item(access_token, item_id)
            latency = time.time() - start_time
            ml_closed = ok
            ml_detail = detail
            db.add(ExternalCallLog(
                kind="ml_item_delete",
                target_url=f"https://api.mercadolibre.com/items/{item_id}",
                status_code=200 if ok else 400,
                success=ok,
                latency_seconds=latency,
                detail={"product_id": product_id, "credential_id": credential_id, "closed": ok},
            ))
        except HTTPException as e:
            # Falha ao encerrar no ML não impede a remoção local, mas é reportada.
            ml_detail = {"error": e.detail}

    title = product.title
    db.delete(product)
    db.commit()

    return {
        "message": f"Produto '{title}' excluído com sucesso.",
        "marketplace_closed": ml_closed,
        "marketplace_detail": ml_detail,
    }
