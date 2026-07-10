import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Header, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional

from backend.app.database import get_db, WebhookEvent, Credential, Product
from backend.app.config import BLING_CLIENT_SECRET
from backend.app.integrations.bling import verify_webhook_signature, find_product_by_sku, get_product_by_id
from backend.app.services.erp_sync_service import sync_product_stock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _extract_sku_and_bling_id(data: dict):
    """Extrai defensivamente o SKU/código e o ID interno do Bling do payload.

    A documentação pública do Bling não define de forma explícita e estável o
    formato exato de 'data' para eventos de estoque/produto, então tentamos
    múltiplos caminhos comuns em vez de assumir um único formato.
    """
    if not isinstance(data, dict):
        return None, None

    sku = data.get("codigo") or data.get("sku")
    bling_id = data.get("id")

    produto = data.get("produto")
    if isinstance(produto, dict):
        sku = sku or produto.get("codigo") or produto.get("sku")
        bling_id = bling_id or produto.get("id")

    return sku, bling_id


@router.post("/bling")
async def bling_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_bling_signature_256: Optional[str] = Header(None, alias="X-Bling-Signature-256"),
):
    """Recebe eventos de webhook do Bling ERP (estoque/produto atualizados).

    Endpoint público (sem JWT) — a autenticidade é garantida pela assinatura
    HMAC-SHA256 no header X-Bling-Signature-256, calculada com o
    BLING_CLIENT_SECRET do app registrado no Bling Developers.

    Sempre responde rápido com 2xx para eventos válidos (mesmo quando não
    localiza o produto correspondente), conforme exigido pelo Bling — do
    contrário ele reenvia o mesmo evento por até 3 dias.
    """
    raw_body = await request.body()

    if not verify_webhook_signature(raw_body, x_bling_signature_256, BLING_CLIENT_SECRET):
        logger.warning("Webhook do Bling com assinatura inválida ou ausente.")
        # 401 é intencional aqui (diferente de erro de processamento): assinatura
        # inválida nunca vai "se resolver sozinha" com retry, mas não deve gerar
        # log de evento (poderia ser payload malicioso, não um evento real).
        return JSONResponse(status_code=401, content={"detail": "Assinatura inválida."})

    try:
        payload = json.loads(raw_body)
    except Exception:
        logger.error("Webhook do Bling com corpo que não é JSON válido.")
        return JSONResponse(status_code=400, content={"detail": "Payload inválido."})

    event_id = str(payload.get("eventId") or "")
    event_type = payload.get("event")
    company_id = str(payload.get("companyId") or "") or None
    data = payload.get("data") or {}

    if not event_id:
        return JSONResponse(status_code=400, content={"detail": "eventId ausente."})

    # Idempotência: Bling pode reenviar o mesmo evento múltiplas vezes.
    existing = db.query(WebhookEvent).filter(WebhookEvent.external_event_id == event_id).first()
    if existing:
        return {"received": True, "duplicate": True}

    webhook_event = WebhookEvent(
        provider="bling",
        external_event_id=event_id,
        event_type=event_type,
        company_id=company_id,
        raw_payload=payload,
        status="received",
        received_at=datetime.now(timezone.utc),
    )
    db.add(webhook_event)
    db.commit()
    db.refresh(webhook_event)

    # Só reagimos a eventos de estoque/produto; outros tipos apenas ficam registrados.
    is_relevant = bool(event_type) and (
        event_type.startswith("stock") or event_type.startswith("estoque")
        or event_type.startswith("product") or event_type.startswith("produto")
    )
    if not is_relevant:
        webhook_event.status = "no_match"
        webhook_event.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"received": True}

    sku, bling_id = _extract_sku_and_bling_id(data)
    if not sku and not bling_id:
        webhook_event.status = "no_match"
        webhook_event.error_detail = "Não foi possível extrair SKU/ID do payload."
        webhook_event.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"received": True}

    # Resolve a qual tenant/credencial este evento pertence: como o Bling não
    # documenta de forma estável um endpoint para mapear companyId -> nossa
    # credencial, tentamos a credencial contra o próprio SKU/ID recebido (cada
    # credencial só "enxerga" os produtos da conta Bling à qual pertence).
    bling_creds = db.query(Credential).filter(
        Credential.provider == "bling", Credential.status.in_(["valid", "expired"])
    ).all()

    matched_product = None
    matched_credential = None

    for cred in bling_creds:
        try:
            from backend.app.security.crypto import decrypt_secret
            secret_payload = decrypt_secret(cred.encrypted_secret)
            access_token = secret_payload.get("access_token")
            if not access_token:
                continue

            found_sku = sku
            if not found_sku and bling_id:
                status_p, res_p = get_product_by_id(access_token, int(bling_id))
                if status_p == "success":
                    found_sku = res_p.get("codigo")

            if not found_sku:
                continue

            product = db.query(Product).filter(
                Product.erp_sku == found_sku, Product.tenant_id == cred.tenant_id
            ).first()
            if product:
                matched_product = product
                matched_credential = cred
                break
        except Exception as e:
            logger.warning(f"Erro ao tentar casar webhook Bling com credencial {cred.id}: {e}")
            continue

    if not matched_product or not matched_credential:
        webhook_event.status = "no_match"
        webhook_event.error_detail = f"Nenhum produto local encontrado para SKU={sku} / bling_id={bling_id}."
        webhook_event.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"received": True}

    try:
        sync_product_stock(matched_product.id, matched_credential.id, db, matched_credential.tenant_id)
        webhook_event.status = "processed"
        webhook_event.matched_product_id = matched_product.id
        webhook_event.matched_tenant_id = matched_credential.tenant_id
        webhook_event.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"received": True, "product_id": matched_product.id}
    except Exception as e:
        logger.error(f"Erro ao processar webhook Bling para produto {matched_product.id}: {e}")
        webhook_event.status = "error"
        webhook_event.error_detail = str(e)
        webhook_event.processed_at = datetime.now(timezone.utc)
        db.commit()
        # 200 mesmo em erro de sync: o dado já foi registrado e o scheduler
        # (a cada 15min) cobre esse produto de qualquer forma; não vale a pena
        # o Bling reenviar o mesmo evento por 3 dias por uma falha pontual nossa.
        return {"received": True, "warning": "Falha ao sincronizar, tentativa registrada."}
