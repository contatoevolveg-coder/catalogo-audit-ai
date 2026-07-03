from typing import Optional
from datetime import datetime, timezone, timedelta
import logging
from fastapi import Request
from sqlalchemy.orm import Session
from backend.app.database import AuthAttempt

logger = logging.getLogger(__name__)

def get_client_ip(request: Request) -> str:
    """Extrai o IP real do cliente.
    
    Nota: O header X-Forwarded-For pode ser falsificado em ambientes sem proxy
    reverso configurado, mas é o padrão de mercado para serverless (Vercel).
    """
    x_forwarded = request.headers.get("x-forwarded-for", "")
    if x_forwarded:
        parts = x_forwarded.split(",")
        if parts:
            first_ip = parts[0].strip()
            if first_ip:
                return first_ip
    if request.client:
        return request.client.host
    return "unknown"

def record_attempt(db: Session, action: str, identifier: str, ip_address: Optional[str], success: bool) -> None:
    """Insere e persiste um registro de tentativa de autenticação no banco de dados."""
    attempt = AuthAttempt(
        action=action,
        identifier=identifier,
        ip_address=ip_address,
        success=success
    )
    db.add(attempt)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar AuthAttempt no banco de dados: {e}")
        raise e

def is_locked_out(db: Session, action: str, identifier: str, max_attempts: int, lockout_minutes: int) -> bool:
    """Verifica se o identificador excedeu o limite máximo de tentativas malsucedidas.
    
    Busca todas as tentativas com success=False para a action e o identifier nos últimos
    lockout_minutes minutos.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=lockout_minutes)
    count = db.query(AuthAttempt).filter(
        AuthAttempt.action == action,
        AuthAttempt.identifier == identifier,
        AuthAttempt.success == False,
        AuthAttempt.created_at >= threshold
    ).count()
    return count >= max_attempts
