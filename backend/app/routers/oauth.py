from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
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
    db: Session = Depends(get_db)
):
    """Recebe o authorization code, valida o state, e troca por access_token e refresh_token.

    Cria automaticamente uma Credential válida no cofre, vinculada ao tenant que
    iniciou o fluxo em /authorize (identificado pelo próprio 'state', não por um
    Bearer token nesta requisição).
    """
    try:
        cred = handle_callback(
            provider=provider,
            code=payload.code,
            state=payload.state,
            label=payload.label,
            db=db,
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


@router.get("/{provider}/callback", response_class=HTMLResponse)
def callback_provider_redirect(
    provider: str,
    code: str,
    state: str,
    db: Session = Depends(get_db)
):
    """Endpoint que o navegador acessa diretamente após o redirect do provedor OAuth2.

    O ML/Bling/Shopee redireciona o navegador do usuário (GET, sem Authorization
    header) para OAUTH_REDIRECT_BASE_URL + este caminho, com 'code' e 'state' na
    query string. Completa o mesmo handle_callback usado pelo fluxo POST e exibe
    uma página HTML simples de confirmação.
    """
    try:
        cred = handle_callback(
            provider=provider,
            code=code,
            state=state,
            label=f"{provider} (autorizado via OAuth)",
            db=db
        )
        return f"""
        <html><body style="font-family: sans-serif; text-align: center; padding: 60px;">
            <h2>✅ Credencial conectada com sucesso!</h2>
            <p>Provedor: <b>{cred.provider}</b> — Credencial: <b>{cred.label}</b></p>
            <p>Pode fechar esta aba e voltar ao dashboard.</p>
        </body></html>
        """
    except HTTPException as e:
        return HTMLResponse(
            content=f"""
            <html><body style="font-family: sans-serif; text-align: center; padding: 60px;">
                <h2>❌ Falha ao conectar</h2>
                <p>{e.detail}</p>
                <p style="margin-top: 20px; font-size: 14px; color: #666;">
                    Sugestão: Feche esta aba, volte ao painel, gere um NOVO link de autorização e tente novamente.
                </p>
            </body></html>
            """,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Erro no callback OAuth (GET) para {provider}: {str(e)}")
        return HTMLResponse(
            content=f"""
            <html><body style="font-family: sans-serif; text-align: center; padding: 60px;">
                <h2>❌ Erro interno</h2>
                <p>{str(e)}</p>
            </body></html>
            """,
            status_code=500
        )
