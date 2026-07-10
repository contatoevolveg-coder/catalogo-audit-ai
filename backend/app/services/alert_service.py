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
    # Query em lote (evita N+1 com centenas de produtos publicados).
    try:
        low_stock_products = db.query(Product).filter(
            Product.status == "published",
            Product.tenant_id == tenant_id,
            Product.available_quantity.isnot(None),
            Product.available_quantity <= 5,
        ).all()
        if low_stock_products:
            candidate_ids = [p.id for p in low_stock_products]
            existing_ids = {
                row[0] for row in db.query(Alert.product_id).filter(
                    Alert.type == "low_stock",
                    Alert.is_read == False,
                    Alert.product_id.in_(candidate_ids),
                ).all()
            }
            for prod in low_stock_products:
                if prod.id not in existing_ids:
                    db.add(Alert(
                        tenant_id=tenant_id,
                        type="low_stock",
                        severity="HIGH",
                        product_id=prod.id,
                        message=f"Estoque baixo para o produto '{prod.title}': restam apenas {prod.available_quantity} unidades.",
                        is_read=False,
                        created_at=now,
                    ))
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

    # 3. Sugestão mais recente com seo_score < 50 (query em lote, dedup por produto)
    try:
        low_sugs = db.query(Suggestion).filter(
            Suggestion.seo_score < 50, Suggestion.tenant_id == tenant_id
        ).all()
        product_ids = list({s.product_id for s in low_sugs})
        if product_ids:
            prods = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()}
            # menor score por produto (pior caso)
            score_by_prod = {}
            for s in low_sugs:
                if s.product_id not in score_by_prod or s.seo_score < score_by_prod[s.product_id]:
                    score_by_prod[s.product_id] = s.seo_score
            existing_ids = {
                row[0] for row in db.query(Alert.product_id).filter(
                    Alert.type == "low_seo_score",
                    Alert.is_read == False,
                    Alert.product_id.in_(product_ids),
                ).all()
            }
            for pid in product_ids:
                prod = prods.get(pid)
                if prod and pid not in existing_ids:
                    db.add(Alert(
                        tenant_id=tenant_id,
                        type="low_seo_score",
                        severity="MEDIUM",
                        product_id=pid,
                        message=f"O produto '{prod.title}' possui um SEO score baixo ({score_by_prod[pid]}/100) e precisa de otimização.",
                        is_read=False,
                        created_at=now,
                    ))
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
                    # Padrão ancorado ao formato real da mensagem ("A Pergunta ID {id} (")
                    # para evitar falso-positivo entre IDs prefixo (ex.: 5 casar com 50).
                    existing = db.query(Alert).filter(
                        Alert.type == "answer_pending",
                        Alert.message.like(f"A Pergunta ID {q.id} (%"),
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
