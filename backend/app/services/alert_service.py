import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.app.database import Product, Credential, Suggestion, CustomerQuestion, Alert
from backend.app.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

def check_and_generate_alerts(db: Session, tenant_id: int) -> int:
    """
    Executa as regras de negócios e gera alertas no banco de dados para um tenant específico
    se não existirem alertas não lidos semelhantes.
    Retorna o número de alertas recém-criados.
    """
    now = datetime.utcnow()
    alerts_created = 0
    
    # 1. Estoque de produto publicado <= limite (default 5)
    try:
        products = db.query(Product).filter(Product.status == "published", Product.tenant_id == tenant_id).all()
        for prod in products:
            if prod.available_quantity is not None and prod.available_quantity <= 5:
                existing = db.query(Alert).filter(
                    Alert.type == "low_stock",
                    Alert.product_id == prod.id,
                    Alert.is_read == False
                ).first()
                if not existing:
                    alert = Alert(
                        tenant_id=tenant_id,
                        type="low_stock",
                        severity="HIGH",
                        product_id=prod.id,
                        message=f"Estoque baixo para o produto '{prod.title}': restam apenas {prod.available_quantity} unidades.",
                        is_read=False,
                        created_at=now
                    )
                    db.add(alert)
                    alerts_created += 1
    except Exception as e:
        logger.error(f"Erro ao verificar regra low_stock: {e}")

    # 2. Credencial com token_expires_at a menos de 24h e sem refresh_token
    try:
        creds = db.query(Credential).filter(Credential.status != "error", Credential.tenant_id == tenant_id).all()
        for cred in creds:
            if cred.token_expires_at:
                time_left = (cred.token_expires_at - now).total_seconds()
                if time_left < 86400: # 24h
                    has_refresh = False
                    try:
                        secret_payload = decrypt_secret(cred.encrypted_secret)
                        if secret_payload.get("refresh_token"):
                            has_refresh = True
                    except Exception:
                        pass
                    
                    if not has_refresh:
                        existing = db.query(Alert).filter(
                            Alert.type == "credential_expiring",
                            Alert.credential_id == cred.id,
                            Alert.is_read == False
                        ).first()
                        if not existing:
                            alert = Alert(
                                tenant_id=tenant_id,
                                type="credential_expiring",
                                severity="HIGH",
                                credential_id=cred.id,
                                message=f"A credencial '{cred.label}' irá expirar em menos de 24h e não possui refresh token.",
                                is_read=False,
                                created_at=now
                            )
                            db.add(alert)
                            alerts_created += 1
    except Exception as e:
        logger.error(f"Erro ao verificar regra credential_expiring: {e}")

    # 3. Sugestão mais recente com seo_score < 50
    try:
        suggestions = db.query(Suggestion).filter(Suggestion.seo_score < 50, Suggestion.tenant_id == tenant_id).all()
        for sug in suggestions:
            prod = db.query(Product).filter(Product.id == sug.product_id).first()
            if prod:
                existing = db.query(Alert).filter(
                    Alert.type == "low_seo_score",
                    Alert.product_id == prod.id,
                    Alert.is_read == False
                ).first()
                if not existing:
                    alert = Alert(
                        tenant_id=tenant_id,
                        type="low_seo_score",
                        severity="MEDIUM",
                        product_id=prod.id,
                        message=f"O produto '{prod.title}' possui um SEO score baixo ({sug.seo_score}/100) e precisa de otimização.",
                        is_read=False,
                        created_at=now
                    )
                    db.add(alert)
                    alerts_created += 1
    except Exception as e:
        logger.error(f"Erro ao verificar regra low_seo_score: {e}")

    # 4. Pergunta pending_draft/draft_ready há mais de 2 horas sem resposta
    try:
        questions = db.query(CustomerQuestion).filter(
            CustomerQuestion.status.in_(["pending_draft", "draft_ready"]),
            CustomerQuestion.tenant_id == tenant_id
        ).all()
        for q in questions:
            if q.fetched_at:
                elapsed = (now - q.fetched_at).total_seconds() / 3600.0
                if elapsed > 2.0: # 2 horas
                    existing = db.query(Alert).filter(
                        Alert.type == "answer_pending",
                        Alert.message.like(f"%ID {q.id}%"),
                        Alert.is_read == False
                    ).first()
                    if not existing:
                        text_preview = q.question_text[:30] + "..." if len(q.question_text) > 30 else q.question_text
                        alert = Alert(
                            tenant_id=tenant_id,
                            type="answer_pending",
                            severity="MEDIUM",
                            message=f"A Pergunta ID {q.id} ('{text_preview}') aguarda resposta há mais de {int(elapsed)} horas.",
                            is_read=False,
                            created_at=now
                        )
                        db.add(alert)
                        alerts_created += 1
    except Exception as e:
        logger.error(f"Erro ao verificar regra answer_pending: {e}")
                
    if alerts_created > 0:
        db.commit()
        
    return alerts_created
