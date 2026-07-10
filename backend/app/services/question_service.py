from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import CustomerQuestion, Credential, Product, ExternalCallLog, Alert
from backend.app.security.crypto import decrypt_secret
from backend.app.integrations.mercado_livre import fetch_unanswered_questions, submit_answer
from backend.app.agent import draft_question_answer
from backend.app.services.oauth_service import refresh_if_needed

logger = logging.getLogger(__name__)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _parse_iso_datetime(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        s = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        try:
            from dateutil.parser import parse
            return parse(date_str)
        except Exception:
            return None

def _escalate_to_human(question: CustomerQuestion, db: Session, reason: str) -> None:
    reason_label = ""
    if reason == "draft_failed":
        reason_label = "IA não conseguiu gerar rascunho"
    elif reason == "low_confidence":
        reason_label = "IA não teve confiança na resposta e pediu revisão"
    elif reason == "send_failed":
        reason_label = "Falha ao enviar a resposta ao Mercado Livre"
    
    # Dedup
    existing_alert = db.query(Alert).filter(
        Alert.type == "ai_answer_failed",
        Alert.message.like(f"Pergunta ID {question.id} (%"),
        Alert.is_read == False,
        Alert.tenant_id == question.tenant_id
    ).first()
    
    if existing_alert:
        return
        
    preview = question.question_text[:30] + "..." if len(question.question_text) > 30 else question.question_text
    
    alert = Alert(
        tenant_id=question.tenant_id,
        type="ai_answer_failed",
        severity="HIGH",
        message=f"Pergunta ID {question.id} ('{preview}') precisa de resposta manual — motivo: {reason_label}.",
        is_read=False
    )
    db.add(alert)
    db.commit()

def sync_pending_questions(credential_id: int, db: Session, tenant_id: int, max_fetch: int = 20) -> Dict[str, int]:
    """Sincroniza perguntas não respondidas do Mercado Livre no banco de dados local."""
    # 1. Busca a credencial no cofre
    cred = db.query(Credential).filter(Credential.id == credential_id, Credential.tenant_id == tenant_id).first()
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credencial não encontrada no cofre."
        )

    if cred.provider != "mercado_livre":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A credencial informada não pertence ao Mercado Livre."
        )

    if cred.status not in ["valid", "expired"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A credencial informada não está válida."
        )

    cred = refresh_if_needed(cred, db)
    if cred.status != "valid":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na renovação da credencial: {cred.status_detail}"
        )

    # 2. Descriptografa o access token
    secret = decrypt_secret(cred.encrypted_secret)
    access_token = secret.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de acesso ausente na credencial."
        )

    # 3. Chama a API do Mercado Livre
    raw_questions = fetch_unanswered_questions(access_token)

    synced = 0
    skipped_existing = 0

    for q in raw_questions:
        ml_id = str(q.get("id"))
        
        # Verifica se já existe localmente
        exists = db.query(CustomerQuestion).filter(
            CustomerQuestion.ml_question_id == ml_id,
            CustomerQuestion.tenant_id == tenant_id
        ).first()
        if exists:
            skipped_existing += 1
            continue

        if synced >= max_fetch:
            break

        item_id = str(q.get("item_id"))
        
        # Tenta casar com algum produto local que possua o external_listing_id correspondente
        prod = db.query(Product).filter(
            Product.external_listing_id == item_id,
            Product.tenant_id == tenant_id
        ).first()

        ml_created_at = _parse_iso_datetime(q.get("date_created"))

        new_q = CustomerQuestion(
            tenant_id=tenant_id,
            credential_id=credential_id,
            ml_question_id=ml_id,
            item_id=item_id,
            matched_product_id=prod.id if prod else None,
            question_text=q.get("text", ""),
            asker_nickname=q.get("from", {}).get("nickname") if isinstance(q.get("from"), dict) else None,
            status="pending_draft",
            ml_created_at=ml_created_at,
            fetched_at=_utcnow()
        )
        db.add(new_q)
        synced += 1

    db.commit()
    return {"synced": synced, "skipped_existing": skipped_existing}

def generate_draft_answer(question_id: int, db: Session, tenant_id: int) -> CustomerQuestion:
    """Gera um rascunho de resposta para a pergunta pré-venda usando o Gemini."""
    question = db.query(CustomerQuestion).filter(CustomerQuestion.id == question_id, CustomerQuestion.tenant_id == tenant_id).first()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pergunta não encontrada."
        )

    product_context = None
    if question.matched_product_id:
        prod = db.query(Product).filter(Product.id == question.matched_product_id, Product.tenant_id == tenant_id).first()
        if prod:
            product_context = {
                "title": prod.title,
                "description": prod.description,
                "price": prod.price,
                "attributes": prod.attributes
            }

    # Gera a sugestão de resposta via Gemini
    try:
        suggested_answer, needs_human_review, review_reason, tokens_in, tokens_out, latency = draft_question_answer(
            question.question_text,
            product_context,
            "mercado_livre"
        )
    except Exception as e:
        logger.exception(f"Erro ao gerar rascunho de resposta (pergunta ID {question.id}): {e}")
        question.status = "error"
        question.review_reason = "Falha ao gerar rascunho (motor de IA indisponível)."
        _escalate_to_human(question, db, "draft_failed")
        db.commit()
        return question

    question.ai_suggested_answer = suggested_answer
    question.needs_human_review = needs_human_review
    question.review_reason = review_reason
    question.tokens_input = tokens_in
    question.tokens_output = tokens_out
    question.latency_seconds = latency
    question.status = "draft_ready"

    if needs_human_review:
        _escalate_to_human(question, db, "low_confidence")

    db.commit()
    db.refresh(question)
    return question

def send_answer(question_id: int, credential_id: int, db: Session, tenant_id: int, final_text: Optional[str] = None) -> CustomerQuestion:
    """Envia a resposta final (original ou modificada) ao Mercado Livre."""
    question = db.query(CustomerQuestion).filter(CustomerQuestion.id == question_id, CustomerQuestion.tenant_id == tenant_id).first()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pergunta não encontrada."
        )

    # Determina o texto a enviar
    text_to_send = final_text
    if not text_to_send:
        text_to_send = question.ai_suggested_answer

    if not text_to_send:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gere um rascunho ou informe o texto final antes de enviar."
        )

    # Busca a credencial no cofre
    cred = db.query(Credential).filter(Credential.id == credential_id, Credential.tenant_id == tenant_id).first()
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credencial não encontrada no cofre."
        )

    if cred.provider != "mercado_livre":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A credencial informada não pertence ao Mercado Livre."
        )

    if cred.status not in ["valid", "expired"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A credencial informada não está válida."
        )

    cred = refresh_if_needed(cred, db)
    if cred.status != "valid":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na renovação da credencial: {cred.status_detail}"
        )

    # Descriptografa o token
    secret = decrypt_secret(cred.encrypted_secret)
    access_token = secret.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de acesso ausente na credencial."
        )

    # Envia de verdade ao Mercado Livre
    start_time = datetime.now(timezone.utc)
    success, body = submit_answer(access_token, question.ml_question_id, text_to_send)
    latency = (datetime.now(timezone.utc) - start_time).total_seconds()

    if success:
        question.status = "approved_sent"
        question.final_answer_text = text_to_send
        question.answered_at = _utcnow()
    else:
        # Se for erro de credencial expirada (401), invalida a credencial no cofre
        error_msg = str(body.get("error", "")).lower()
        detail_msg = str(body.get("detail", "")).lower()
        is_401 = "401" in error_msg or "unauthorized" in error_msg or "token" in error_msg or "401" in detail_msg
        
        question.status = "error"
        if is_401:
            cred.status = "expired"
            cred.status_detail = "Token expirado ou inválido (401 na resposta ao responder pergunta)"
            question.review_reason = "Token expirado no Mercado Livre (401)."
        else:
            question.review_reason = f"Erro no Mercado Livre: {body.get('error', 'Erro desconhecido')}"
            _escalate_to_human(question, db, "send_failed")

    # Grava no log de chamadas externas sem vazar o token
    log_detail = {
        "question_id": question.id,
        "ml_question_id": question.ml_question_id,
        "response_body": body
    }

    db_log = ExternalCallLog(
        kind="ml_answer_submit",
        target_url="https://api.mercadolibre.com/answers",
        status_code=200 if success else 400,
        success=success,
        latency_seconds=latency,
        detail=log_detail
    )
    db.add(db_log)
    db.commit()
    db.refresh(question)
    return question

def dismiss_question(question_id: int, db: Session, tenant_id: int) -> CustomerQuestion:
    """Descarta a pergunta no sistema local sem fazer nenhuma chamada externa ao Mercado Livre."""
    question = db.query(CustomerQuestion).filter(CustomerQuestion.id == question_id, CustomerQuestion.tenant_id == tenant_id).first()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pergunta não encontrada."
        )

    question.status = "dismissed"
    db.commit()
    db.refresh(question)
    return question
