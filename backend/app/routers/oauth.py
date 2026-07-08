from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from backend.app.database import get_db, Credential
from backend.app.schemas import AuthorizationUrlResponse, OAuthCallbackRequest, CredentialResponse
from backend.app.services.oauth_service import start_authorization, handle_callback
from backend.app.security.dependencies import get_current_user, get_current_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["OAuth"])

@router.get("/{provider}/authorize", response_model=AuthorizationUrlResponse)
def authorize_provider(
    provider: str,
    db: Session = Depends(get_db),
    # Exige que o usuário esteja logado para iniciar o fluxo
    tenant_id: int = Depends(get_current_tenant)
):
    """Gera a URL de autorização OAuth2 para o provedor especificado.
    
    provider: 'mercado_livre' ou 'bling'
    """
    try:
        url = start_authorization(provider, db, tenant_id)
        return {"authorization_url": url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao iniciar autorização para {provider}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno ao iniciar autorização: {str(e)}"
        )

@router.post("/{provider}/callback", response_model=CredentialResponse)
def callback_provider(
    provider: str,
    payload: OAuthCallbackRequest,
    db: Session = Depends(get_db),
    # Exige que o usuário esteja logado para associar a credencial
    tenant_id: int = Depends(get_current_tenant)
):
    """Recebe o authorization code, valida o state, e troca por access_token e refresh_token.
    
    Cria automaticamente uma Credential válida no cofre.
    """
    try:
        cred = handle_callback(
            provider=provider,
            code=payload.code,
            state=payload.state,
            label=payload.label,
            db=db,
            tenant_id=tenant_id,
            shop_id=payload.shop_id
        )
        return cred
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no callback OAuth para {provider}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno no processamento do callback: {str(e)}"
        )
