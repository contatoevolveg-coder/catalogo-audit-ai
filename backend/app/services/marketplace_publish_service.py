import time
import logging
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import Product, Suggestion, Credential, MarketplacePublication, ExternalCallLog
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations.mercado_livre import publish_item as ml_publish_item, publish_item_description
from backend.app.integrations.shopee import publish_item as shopee_publish_item
from backend.app.config import SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY
from backend.app.constants import DEFAULT_ML_LISTING_TYPE
from backend.app.services.oauth_service import refresh_if_needed

logger = logging.getLogger(__name__)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def publish_product_to_ml(
    product_id: int,
    credential_id: int,
    category_id: str,
    db: Session,
    tenant_id: int
) -> MarketplacePublication:
    """Publica um anúncio no Mercado Livre utilizando as credenciais da Fase 3 e sugestões aprovadas da Fase 0.

    Segue regras estritas de duplo portão de aprovação humana e segurança de chaves.
    """
    # 1. Busca o produto
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produto não encontrado."
        )

    # 2. Busca a Suggestion mais recente do produto
    suggestion = db.query(Suggestion)\
        .filter(Suggestion.product_id == product_id, Suggestion.tenant_id == tenant_id)\
        .order_by(Suggestion.id.desc())\
        .first()

    if not suggestion or suggestion.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Produto precisa ter uma sugestão aprovada antes de ser publicado."
        )

    # 3. Busca a Credential
    credential = db.query(Credential).filter(Credential.id == credential_id, Credential.tenant_id == tenant_id).first()
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credencial não encontrada."
        )

    if credential.provider != "mercado_livre" or credential.status not in ["valid", "expired"]:
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

    # 6. Monta o payload (Título truncado a 60 chars)
    title = (suggestion.suggested_title or "").strip()
    if len(title) > 60:
        title = title[:60]

    # Montagem dos atributos (Best-effort)
    attrs = []
    if isinstance(product.attributes, dict):
        brand = product.attributes.get("brand") or product.attributes.get("BRAND")
        if brand:
            attrs.append({"id": "BRAND", "value_name": str(brand)})
        model = product.attributes.get("model") or product.attributes.get("MODEL")
        if model:
            attrs.append({"id": "MODEL", "value_name": str(model)})
        gtin = product.attributes.get("gtin_ean") or product.attributes.get("GTIN")
        if gtin:
            attrs.append({"id": "GTIN", "value_name": str(gtin)})

    payload = {
        "title": title,
        "category_id": category_id,
        "price": product.price,
        "currency_id": "BRL",
        "available_quantity": product.available_quantity or 1,
        "buying_mode": "buy_it_now",
        "condition": product.condition or "new",
        "listing_type_id": DEFAULT_ML_LISTING_TYPE,
        "pictures": [{"source": url} for url in (product.images or [])][:10],
    }

    if attrs:
        payload["attributes"] = attrs

    # Payload para salvar no banco (SEM chaves de autenticação)
    db_request_payload = {
        "title": payload["title"],
        "category_id": payload["category_id"],
        "price": payload["price"],
        "currency_id": payload["currency_id"],
        "available_quantity": payload["available_quantity"],
        "buying_mode": payload["buying_mode"],
        "condition": payload["condition"],
        "listing_type_id": payload["listing_type_id"],
        "pictures_count": len(payload["pictures"]),
        "attributes": attrs
    }

    # 6. Chama publish_item
    start_time = time.time()
    success, ml_response = ml_publish_item(access_token, payload)
    latency = time.time() - start_time

    pub = MarketplacePublication(
        tenant_id=tenant_id,
        product_id=product_id,
        credential_id=credential_id,
        category_id=category_id,
        request_payload=db_request_payload,
        response_payload=ml_response,
        created_at=_utcnow()
    )

    # Tratamento dos resultados
    status_code_ml = ml_response.get("status") if "status" in ml_response else None

    # Caso de erro do ML
    if not success:
        pub.status = "error"
        # Se for erro 401 de autenticação do próprio ML
        if ml_response.get("error") == "unauthorized" or ml_response.get("status") == 401:
            credential.status = "expired"
            credential.status_detail = "Token expirado ou inválido (retorno 401 da publicação)"
            credential.updated_at = _utcnow()
            pub.error_detail = "Token de acesso expirado ou inválido (HTTP 401)."
        else:
            # Caso de erro geral do ML
            pub.error_detail = str(ml_response.get("message") or ml_response.get("error") or ml_response)
    else:
        # Sucesso na criação do item
        pub.status = "success"
        pub.marketplace_item_id = ml_response.get("id")
        
        # Atualiza status do produto
        product.external_listing_id = pub.marketplace_item_id
        product.status = "published"
        product.marketplace_status = "active"

        # Tenta enviar a descrição na segunda chamada (operação best-effort)
        desc_success, desc_response = publish_item_description(
            access_token, pub.marketplace_item_id, suggestion.suggested_description
        )
        if not desc_success:
            pub.error_detail = f"Anúncio criado, mas falhou ao enviar descrição: {str(desc_response)}"

    # 8. Registra chamada no ExternalCallLog (NUNCA incluir token em detail!)
    db_log = ExternalCallLog(
        kind="ml_publish",
        target_url="https://api.mercadolibre.com/items",
        status_code=201 if success else 400,
        success=success,
        latency_seconds=latency,
        detail={
            "product_id": product_id,
            "credential_id": credential_id,
            "status": pub.status,
            "error_detail": pub.error_detail,
            "marketplace_item_id": pub.marketplace_item_id
        }
    )
    
    db.add(pub)
    db.add(db_log)
    db.commit()
    db.refresh(pub)

    return pub

def publish_product_to_shopee(
    product_id: int,
    credential_id: int,
    category_id: int,
    db: Session,
    tenant_id: int
) -> MarketplacePublication:
    """Publica um anúncio na Shopee utilizando as credenciais da Fase 3 e sugestões aprovadas da Fase 0."""
    
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")

    suggestion = db.query(Suggestion).filter(Suggestion.product_id == product_id, Suggestion.tenant_id == tenant_id).order_by(Suggestion.id.desc()).first()
    if not suggestion or suggestion.status != "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Produto precisa ter uma sugestão aprovada antes de ser publicado.")

    credential = db.query(Credential).filter(Credential.id == credential_id, Credential.tenant_id == tenant_id).first()
    if not credential:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credencial não encontrada.")

    if credential.provider != "shopee" or credential.status not in ["valid", "expired"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Credencial da Shopee não está válida.")

    credential = refresh_if_needed(credential, db)
    if credential.status != "valid":
         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Falha na renovação da credencial: {credential.status_detail}")

    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
        access_token = secret_payload.get("access_token")
        shop_id = secret_payload.get("shop_id")
        if not access_token or not shop_id:
            raise ValueError("Token de acesso ou shop_id ausentes na credencial.")
    except Exception as e:
        logger.error(f"Erro ao decriptografar credencial: {str(e)}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Falha ao decriptografar chave de acesso: {str(e)}")

    if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SHOPEE_PARTNER_ID e SHOPEE_PARTNER_KEY não configurados.")

    title = (suggestion.suggested_title or "").strip()
    if len(title) > 120:
        title = title[:120]

    # Payload simplificado para Shopee Open Platform V2
    payload = {
        "original_price": product.price,
        "description": suggestion.suggested_description or "Descrição",
        "weight": 1.0,
        "item_name": title,
        "item_status": "NORMAL",
        "normal_stock": product.available_quantity or 1,
        "category_id": int(category_id),
        "logistic_info": [{"logistic_id": 10004, "enabled": True}], # Logistic genérico fallback
        "image": {"image_id_list": []}
    }

    db_request_payload = payload.copy()

    start_time = time.time()
    success, shopee_response = shopee_publish_item(
        int(SHOPEE_PARTNER_ID),
        SHOPEE_PARTNER_KEY,
        access_token,
        int(shop_id),
        payload
    )
    latency = time.time() - start_time

    pub = MarketplacePublication(
        tenant_id=tenant_id,
        product_id=product_id,
        credential_id=credential_id,
        category_id=str(category_id),
        request_payload=db_request_payload,
        response_payload=shopee_response,
        created_at=_utcnow()
    )

    if not success:
        pub.status = "error"
        if shopee_response.get("error") in ["error_auth", "error_token_expired"]:
            credential.status = "expired"
            credential.status_detail = "Token expirado ou inválido (retorno Auth Shopee)"
            credential.updated_at = _utcnow()
            pub.error_detail = "Token de acesso expirado ou inválido."
        else:
            pub.error_detail = shopee_response.get("message") or shopee_response.get("error") or str(shopee_response)
    else:
        pub.status = "success"
        # O ID do item retornado pela Shopee V2 geralmente fica em response.response.item_id
        resp_data = shopee_response.get("response", {})
        pub.marketplace_item_id = str(resp_data.get("item_id", ""))

        product.external_listing_id = pub.marketplace_item_id
        product.status = "published"
        product.marketplace_status = "active"

    db_log = ExternalCallLog(
        kind="shopee_publish",
        target_url="https://partner.shopeemobile.com/api/v2/product/add_item",
        status_code=200 if success else 400,
        success=success,
        latency_seconds=latency,
        detail={
            "product_id": product_id,
            "credential_id": credential_id,
            "status": pub.status,
            "error_detail": pub.error_detail,
            "marketplace_item_id": pub.marketplace_item_id
        }
    )
    
    db.add(pub)
    db.add(db_log)
    db.commit()
    db.refresh(pub)

    return pub
