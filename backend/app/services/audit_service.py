import os
import logging
from sqlalchemy.orm import Session

from backend.app.constants import ProductStatus, SuggestionStatus
from backend.app.database import Product, Suggestion, AuditLog
from backend.app.agent import audit_product_with_gemini

logger = logging.getLogger(__name__)

def perform_product_audit(product: Product, db: Session) -> Suggestion:
    """Executa a auditoria de um produto via Gemini e persiste log e sugestão.

    Função de serviço reutilizada tanto pelo endpoint individual quanto pelo
    de lote — evita chamar handlers de rota diretamente. Comita a transação.
    """
    input_payload = {
        "title": product.title,
        "description": product.description,
        "images": product.images or [],
        "category": product.category,
        "price": product.price,
        "marketplace": product.marketplace,
    }

    audit_result, tokens_in, tokens_out, latency = audit_product_with_gemini(
        title=product.title,
        description=product.description,
        images=product.images or [],
        category=product.category or "",
        price=product.price or 0.0,
        marketplace=product.marketplace,
    )

    output_payload = audit_result.model_dump()

    # 1. Salva log de Auditoria
    db_log = AuditLog(
        product_id=product.id,
        input_payload=input_payload,
        output_payload=output_payload,
        model_used=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        token_cost_usd=0.0,  # Tier gratuito do Google AI Studio
        latency_seconds=latency,
    )
    db.add(db_log)

    # 2. Remove sugestão anterior (se houver) e cria a nova
    existing_suggestion = db.query(Suggestion).filter(Suggestion.product_id == product.id).first()
    if existing_suggestion:
        db.delete(existing_suggestion)

    db_suggestion = Suggestion(
        product_id=product.id,
        suggested_title=audit_result.suggested_title,
        suggested_description=audit_result.suggested_description,
        missing_attributes=output_payload.get("missing_attributes", []),
        image_issues=output_payload.get("image_issues", []),
        seo_score=audit_result.seo_score,
        status=SuggestionStatus.PENDING.value,
    )
    db.add(db_suggestion)

    # 3. Atualiza o status do produto
    product.status = ProductStatus.AUDITED.value

    db.commit()
    db.refresh(db_suggestion)
    return db_suggestion
