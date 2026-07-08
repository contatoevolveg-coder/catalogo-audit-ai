from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db, User, CustomerQuestion
from backend.app.security.dependencies import get_current_user, get_current_tenant
from backend.app.schemas import (
    SyncQuestionsRequest,
    SendAnswerRequest,
    CustomerQuestionResponse
)
from backend.app.services import question_service

router = APIRouter(
    prefix="/marketplace-integrations/mercado-livre/questions",
    tags=["Perguntas Pré-venda Mercado Livre"]
)

@router.post(
    "/sync",
    status_code=status.HTTP_200_OK
)
def sync_questions(
    data: SyncQuestionsRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Sincroniza as perguntas não respondidas do Mercado Livre no banco local."""
    return question_service.sync_pending_questions(
        credential_id=data.credential_id,
        db=db,
        tenant_id=tenant_id,
        max_fetch=data.max_fetch
    )

@router.get(
    "/",
    response_model=List[CustomerQuestionResponse]
)
def list_questions(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Lista as perguntas registradas no sistema local, podendo filtrar por status."""
    query = db.query(CustomerQuestion).filter(CustomerQuestion.tenant_id == tenant_id)
    if status:
        query = query.filter(CustomerQuestion.status == status)
    return query.order_by(CustomerQuestion.id.desc()).all()

@router.get(
    "/{question_id}",
    response_model=CustomerQuestionResponse
)
def get_question(
    question_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Retorna os detalhes de uma pergunta pré-venda local."""
    question = db.query(CustomerQuestion).filter(CustomerQuestion.id == question_id, CustomerQuestion.tenant_id == tenant_id).first()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pergunta não encontrada."
        )
    return question

@router.post(
    "/{question_id}/draft",
    response_model=CustomerQuestionResponse
)
def generate_draft(
    question_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Gera o rascunho de resposta sugerida para a pergunta pré-venda via Gemini."""
    return question_service.generate_draft_answer(
        question_id=question_id,
        db=db,
        tenant_id=tenant_id
    )

@router.post(
    "/{question_id}/send",
    response_model=CustomerQuestionResponse
)
def send_reply(
    question_id: int,
    data: SendAnswerRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Envia a resposta final para o Mercado Livre. Permite revisão humana."""
    return question_service.send_answer(
        question_id=question_id,
        credential_id=data.credential_id,
        db=db,
        tenant_id=tenant_id,
        final_text=data.final_text
    )

@router.post(
    "/{question_id}/dismiss",
    response_model=CustomerQuestionResponse
)
def dismiss(
    question_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Descarte local da pergunta sem enviar resposta."""
    return question_service.dismiss_question(
        question_id=question_id,
        db=db,
        tenant_id=tenant_id
    )
