from datetime import datetime, timezone
from typing import List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import Credential
from backend.app.schemas import CredentialCreate, CredentialUpdate
from backend.app.security.crypto import encrypt_secret, decrypt_secret, generate_masked_preview
from backend.app.integrations import check_credential

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def create_credential(db: Session, data: CredentialCreate) -> Credential:
    """Cria uma nova credencial criptografada e a persiste no banco de dados."""
    # 1. Criptografa o payload do segredo
    encrypted = encrypt_secret(data.secret_payload)
    # 2. Gera a pré-visualização mascarada
    masked = generate_masked_preview(data.secret_payload)

    db_credential = Credential(
        provider=data.provider,
        provider_type=data.provider_type,
        label=data.label,
        encrypted_secret=encrypted,
        masked_preview=masked,
        scopes=data.scopes,
        status="untested",
        status_detail=None,
        last_checked_at=None,
    )

    db.add(db_credential)
    db.commit()
    db.refresh(db_credential)
    return db_credential

def list_credentials(db: Session) -> List[Credential]:
    """Lista todas as credenciais cadastradas."""
    return db.query(Credential).order_by(Credential.id.desc()).all()

def get_credential(db: Session, cred_id: int) -> Credential:
    """Retorna uma credencial pelo ID ou levanta 404."""
    credential = db.query(Credential).filter(Credential.id == cred_id).first()
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credencial não encontrada."
        )
    return credential

def update_credential(db: Session, cred_id: int, data: CredentialUpdate) -> Credential:
    """Atualiza metadados ou rotaciona o segredo de uma credencial existente."""
    credential = get_credential(db, cred_id)

    if data.label is not None:
        credential.label = data.label
    
    if data.scopes is not None:
        credential.scopes = data.scopes

    # Se houver rotação de segredo
    if data.secret_payload is not None:
        credential.encrypted_secret = encrypt_secret(data.secret_payload)
        credential.masked_preview = generate_masked_preview(data.secret_payload)
        # Reseta o status de conexão pois a chave mudou
        credential.status = "untested"
        credential.status_detail = None
        credential.last_checked_at = None

    credential.updated_at = _utcnow()
    db.commit()
    db.refresh(credential)
    return credential

def delete_credential(db: Session, cred_id: int) -> None:
    """Remove fisicamente uma credencial do banco de dados."""
    credential = get_credential(db, cred_id)
    db.delete(credential)
    db.commit()

def test_connectivity(db: Session, cred_id: int) -> Credential:
    """Decriptografa a credencial e testa sua conectividade junto ao provedor."""
    credential = get_credential(db, cred_id)

    # 1. Recupera o segredo decriptografando-o em memória
    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
    except Exception as e:
        credential.status = "error"
        credential.status_detail = f"Falha ao decriptografar chave: {str(e)}"
        credential.last_checked_at = _utcnow()
        db.commit()
        db.refresh(credential)
        return credential

    # 2. Executa a integração para testar a conectividade
    status_res, status_detail = check_credential(credential.provider, secret_payload, db)

    # 3. Atualiza os metadados de conectividade no banco
    credential.status = status_res
    credential.status_detail = status_detail
    credential.last_checked_at = _utcnow()
    credential.updated_at = _utcnow()
    
    db.commit()
    db.refresh(credential)
    return credential
