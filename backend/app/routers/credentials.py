from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db, User
from backend.app.schemas import (
    CredentialCreate, CredentialUpdate, CredentialResponse, CredentialTestResponse
)
from backend.app.services import credential_service
from backend.app import config
from backend.app.security.dependencies import get_current_user, get_current_tenant

router = APIRouter(prefix="/credentials", tags=["Credentials"])

def verify_admin_key(x_admin_key: Optional[str] = Header(None)):
    """Mantido apenas temporariamente por compatibilidade retroativa com importações
    de outros roteadores se houver, mas os endpoints agora usam autenticação JWT.
    """
    if not config.is_admin_key_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Área administrativa não configurada"
        )
    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de acesso administrativo inválida"
        )

@router.post("", response_model=CredentialResponse)
def create(
    data: CredentialCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Cadastra uma nova credencial criptografada."""
    return credential_service.create_credential(db, data, tenant_id)

@router.get("", response_model=List[CredentialResponse])
def list_all(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Lista todas as credenciais registradas (apenas preview mascarado)."""
    return credential_service.list_credentials(db, tenant_id)

@router.get("/{cred_id}", response_model=CredentialResponse)
def get_one(
    cred_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Retorna os detalhes de uma credencial específica (mascarada)."""
    return credential_service.get_credential(db, cred_id, tenant_id)

@router.patch("/{cred_id}", response_model=CredentialResponse)
def update(
    cred_id: int,
    data: CredentialUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Atualiza metadados ou rotaciona o segredo de uma credencial."""
    return credential_service.update_credential(db, cred_id, data, tenant_id)

@router.delete("/{cred_id}", status_code=status.HTTP_200_OK)
def delete(
    cred_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Exclui definitivamente uma credencial."""
    credential_service.delete_credential(db, cred_id, tenant_id)
    return {"message": "Credencial excluída com sucesso."}

@router.post("/{cred_id}/test", response_model=CredentialTestResponse)
def test_connection(
    cred_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Testa a conectividade da credencial e atualiza seu status no banco de dados."""
    cred = credential_service.test_connectivity(db, cred_id, tenant_id)
    return CredentialTestResponse(
        status=cred.status,
        status_detail=cred.status_detail,
        last_checked_at=cred.last_checked_at
    )
