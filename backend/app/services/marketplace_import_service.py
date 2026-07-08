import time
import logging
from datetime import datetime, timezone
from typing import Dict
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import Product, Credential, ExternalCallLog
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations.mercado_livre import (
    get_seller_id, fetch_seller_item_ids, fetch_items_details, fetch_item_description
)
from backend.app.services.oauth_service import refresh_if_needed

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _extract_attributes(item: dict) -> dict:
    """Extrai marca/modelo/gtin da lista de atributos do item do Mercado Livre."""
    attrs = {}
    for attr in item.get("attributes", []) or []:
        attr_id = (attr.get("id") or "").upper()
        value = attr.get("value_name")
        if not value:
            continue
        if attr_id == "BRAND":
            attrs["brand"] = value
        elif attr_id == "MODEL":
            attrs["model"] = value
        elif attr_id in ("GTIN", "EAN"):
            attrs["gtin_ean"] = value
    return attrs


def import_ml_items(credential_id: int, db: Session, tenant_id: int, max_items: int = 100) -> Dict[str, int]:
    """Importa os anúncios já publicados na conta do Mercado Livre para o catálogo local.

    Produtos já vinculados (mesmo external_listing_id) são atualizados
    (preço, estoque, status); os demais são criados como novos Products
    com status 'published', prontos para auditoria/otimização.
    """
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

    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
        access_token = secret_payload.get("access_token")
        if not access_token:
            raise ValueError("Token de acesso ausente na credencial.")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Falha ao decriptografar chave de acesso: {str(e)}")

    start_time = time.time()

    seller_id = get_seller_id(access_token)
    if not seller_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Não foi possível identificar o vendedor no Mercado Livre (token inválido ou expirado).")

    # 1. Pagina sobre os IDs de itens do vendedor até atingir max_items
    item_ids = []
    offset = 0
    page_size = 50
    while len(item_ids) < max_items:
        batch_ids, total = fetch_seller_item_ids(access_token, seller_id, limit=page_size, offset=offset)
        if not batch_ids:
            break
        item_ids.extend(batch_ids)
        offset += page_size
        if offset >= total:
            break
    item_ids = item_ids[:max_items]

    imported = 0
    updated = 0
    skipped_errors = []

    # 2. Busca os detalhes completos em lotes de 20 (limite da API multiget)
    items_details = fetch_items_details(access_token, item_ids)

    for item in items_details:
        try:
            external_id = item.get("id")
            if not external_id:
                continue

            existing = db.query(Product).filter(
                Product.external_listing_id == external_id,
                Product.tenant_id == tenant_id
            ).first()

            pictures = [p.get("secure_url") or p.get("url") for p in (item.get("pictures") or []) if p.get("secure_url") or p.get("url")]
            attrs = _extract_attributes(item)

            if existing:
                existing.price = item.get("price", existing.price)
                existing.available_quantity = item.get("available_quantity", existing.available_quantity)
                existing.status = "published" if item.get("status") == "active" else existing.status
                updated += 1
            else:
                description = fetch_item_description(access_token, external_id)
                new_product = Product(
                    tenant_id=tenant_id,
                    title=item.get("title", "")[:300],
                    description=description,
                    images=pictures,
                    category=item.get("category_id"),
                    price=item.get("price", 0.0),
                    marketplace="mercado_livre",
                    status="published",
                    available_quantity=item.get("available_quantity"),
                    condition=item.get("condition"),
                    attributes=attrs or None,
                    external_listing_id=external_id,
                )
                db.add(new_product)
                imported += 1

        except Exception as e:
            logger.error(f"Erro ao importar item {item.get('id')}: {e}")
            skipped_errors.append({"item_id": item.get("id"), "error": str(e)})

    latency = time.time() - start_time

    db_log = ExternalCallLog(
        kind="ml_import_items",
        target_url="https://api.mercadolibre.com/users/{seller_id}/items/search",
        status_code=200,
        success=True,
        latency_seconds=latency,
        detail={
            "credential_id": credential_id,
            "seller_id": seller_id,
            "found": len(item_ids),
            "imported": imported,
            "updated": updated,
            "errors": len(skipped_errors),
        }
    )
    db.add(db_log)
    db.commit()

    return {
        "found": len(item_ids),
        "imported": imported,
        "updated": updated,
        "errors": skipped_errors,
    }
