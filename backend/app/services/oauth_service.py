import uuid
import hashlib
import base64
import secrets
import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from backend.app.database import OAuthState, Credential
from backend.app.config import (
    ML_CLIENT_ID, ML_CLIENT_SECRET,
    BLING_CLIENT_ID, BLING_CLIENT_SECRET,
    SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY,
    OAUTH_REDIRECT_BASE_URL
)
from backend.app.security.crypto import encrypt_secret, decrypt_secret
from backend.app.integrations import mercado_livre, bling, shopee


def _generate_pkce_pair() -> tuple:
    """Gera (code_verifier, code_challenge) para o fluxo PKCE (S256).

    code_verifier: string aleatória URL-safe (43-128 chars).
    code_challenge: BASE64URL(SHA256(code_verifier)) sem padding.
    """
    code_verifier = secrets.token_urlsafe(64)  # ~86 chars, dentro do limite
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def start_authorization(provider: str, db: Session, tenant_id: int) -> str:
    """Inicia o fluxo OAuth2 gerando um 'state' e a URL de autorização."""
    if provider not in ["mercado_livre", "bling", "shopee"]:
        raise HTTPException(status_code=400, detail="Provedor inválido")

    state_val = str(uuid.uuid4())

    if provider == "mercado_livre":
        if not ML_CLIENT_ID:
            raise HTTPException(status_code=500, detail="ML_CLIENT_ID não configurado")
        # Gera par PKCE e guarda o verifier no state para uso no callback
        code_verifier, code_challenge = _generate_pkce_pair()
        db_state = OAuthState(tenant_id=tenant_id, state=state_val, provider=provider, code_verifier=code_verifier)
        db.add(db_state)
        db.commit()
        redirect_uri = f"{OAUTH_REDIRECT_BASE_URL}/oauth/mercado_livre/callback"
        return mercado_livre.build_authorization_url(ML_CLIENT_ID, redirect_uri, state_val, code_challenge=code_challenge)

    # Demais provedores (sem PKCE)
    db_state = OAuthState(tenant_id=tenant_id, state=state_val, provider=provider)
    db.add(db_state)
    db.commit()

    if provider == "bling":
        if not BLING_CLIENT_ID:
            raise HTTPException(status_code=500, detail="BLING_CLIENT_ID não configurado")
        return bling.build_authorization_url(BLING_CLIENT_ID, state_val)

    elif provider == "shopee":
        if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
            raise HTTPException(status_code=500, detail="Credenciais da Shopee não configuradas")
        redirect_uri = f"{OAUTH_REDIRECT_BASE_URL}/oauth/shopee/callback"
        return shopee.build_authorization_url(SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY, redirect_uri)

def handle_callback(provider: str, code: str, state: str, label: str, db: Session, shop_id: int = None) -> Credential:
    """Processa o callback OAuth2, validando o state e trocando o código pelo token.

    O tenant dono da credencial é o mesmo que iniciou o fluxo em start_authorization
    (recuperado do próprio registro de state) — não depende de um Bearer token na
    requisição de callback, já que o navegador é redirecionado diretamente pelo
    provedor (ML/Bling/Shopee) sem carregar nosso token de sessão.
    """
    state = state.strip() if state else state
    db_state = db.query(OAuthState).filter(OAuthState.state == state).first()
    if not db_state:
        raise HTTPException(status_code=400, detail="State inválido ou expirado")

    if db_state.provider != provider:
        raise HTTPException(status_code=400, detail="State não corresponde ao provedor")

    # Verificar se expirou (30 minutos — dá margem para login lento no provedor)
    time_diff = datetime.datetime.utcnow() - db_state.created_at
    if time_diff.total_seconds() > 1800:
        db.delete(db_state)
        db.commit()
        raise HTTPException(status_code=400, detail="State expirado")

    tenant_id = db_state.tenant_id
    code_verifier = db_state.code_verifier  # PKCE (None para provedores sem PKCE)

    # Troca de código por token
    success = False
    body = {}

    if provider == "mercado_livre":
        redirect_uri = f"{OAUTH_REDIRECT_BASE_URL}/oauth/mercado_livre/callback"
        success, body = mercado_livre.exchange_code_for_token(
            ML_CLIENT_ID, ML_CLIENT_SECRET, code, redirect_uri, code_verifier=code_verifier
        )
        provider_type = "marketplace"
    elif provider == "bling":
        success, body = bling.exchange_code_for_token(
            BLING_CLIENT_ID, BLING_CLIENT_SECRET, code
        )
        provider_type = "erp"
    elif provider == "shopee":
        if not shop_id:
            raise HTTPException(status_code=400, detail="shop_id é obrigatório para a Shopee")
        success, body = shopee.exchange_code_for_token(
            int(SHOPEE_PARTNER_ID), SHOPEE_PARTNER_KEY, code, shop_id
        )
        provider_type = "marketplace"
        
    if not success:
        error_code = body.get("error", "Desconhecido")
        error_msg = body.get("message") or body.get("error_description") or ""
        redirect_hint = ""
        if provider == "mercado_livre" and error_code == "invalid_request":
            redirect_hint = (
                f" (verifique se a Redirect URI cadastrada no app do Mercado Livre é EXATAMENTE "
                f"'{redirect_uri}', sem barra final nem diferença de http/https)"
            )
        raise HTTPException(
            status_code=400,
            detail=f"Erro ao obter token: {error_code}{' - ' + error_msg if error_msg else ''}{redirect_hint}"
        )
        
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    expires_in = body.get("expires_in", 21600)  # ML = 21600 (6h)
    
    if not access_token:
        raise HTTPException(status_code=400, detail="Resposta do provider não contém access_token")
        
    token_expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in)
    
    # Monta payload e criptografa
    secret_payload = {
        "access_token": access_token
    }
    if refresh_token:
        secret_payload["refresh_token"] = refresh_token
    if provider == "shopee" and shop_id:
        secret_payload["shop_id"] = shop_id
        
    encrypted = encrypt_secret(secret_payload)
    
    cred = Credential(
        tenant_id=tenant_id,
        provider=provider,
        provider_type=provider_type,
        label=label,
        encrypted_secret=encrypted,
        masked_preview=f"OAuth ••••{access_token[-4:]}" if len(access_token) >= 4 else "OAuth",
        scopes=["read", "write"],
        status="valid",
        token_expires_at=token_expires_at,
        last_checked_at=datetime.datetime.utcnow()
    )
    db.add(cred)
    
    # Remove o state após sucesso
    db.delete(db_state)
    db.commit()
    
    db.refresh(cred)
    
    return cred

def refresh_if_needed(credential: Credential, db: Session) -> Credential:
    """Verifica se o token vai expirar e faz a renovação se necessário."""
    if credential.provider not in ["mercado_livre", "bling", "shopee"]:
        return credential
        
    # Se não tem expiração definida (ex: credenciais antigas ou testes)
    # ou se ainda falta mais de 5 minutos, não faz refresh
    if not credential.token_expires_at:
        return credential
        
    now = datetime.datetime.utcnow()
    if (credential.token_expires_at - now).total_seconds() > 300:
        return credential
        
    # Precisamos descriptografar para pegar o refresh_token
    try:
        secret_payload = decrypt_secret(credential.encrypted_secret)
    except Exception:
        # Se não conseguir descriptografar, marca como erro
        credential.status = "error"
        credential.status_detail = "Falha ao descriptografar segredo"
        db.commit()
        return credential
        
    refresh_token = secret_payload.get("refresh_token")
    if not refresh_token:
        credential.status = "expired"
        credential.status_detail = "Token expirado e sem refresh_token disponível"
        db.commit()
        return credential
        
    # Tenta renovar
    success = False
    body = {}
    if credential.provider == "mercado_livre":
        success, body = mercado_livre.refresh_access_token(ML_CLIENT_ID, ML_CLIENT_SECRET, refresh_token)
    elif credential.provider == "bling":
        success, body = bling.refresh_access_token(BLING_CLIENT_ID, BLING_CLIENT_SECRET, refresh_token)
    elif credential.provider == "shopee":
        shop_id = secret_payload.get("shop_id")
        success, body = shopee.refresh_access_token(int(SHOPEE_PARTNER_ID), SHOPEE_PARTNER_KEY, refresh_token, shop_id)
        
    if not success:
        credential.status = "expired"
        credential.status_detail = f"Falha na renovação: {body.get('error', 'Desconhecido')}"
        db.commit()
        return credential
        
    # Atualiza as credenciais
    new_access_token = body.get("access_token")
    new_refresh_token = body.get("refresh_token", refresh_token)
    expires_in = body.get("expires_in", 21600)
    
    secret_payload["access_token"] = new_access_token
    secret_payload["refresh_token"] = new_refresh_token
    
    credential.encrypted_secret = encrypt_secret(secret_payload)
    credential.token_expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in)
    credential.masked_preview = f"OAuth ••••{new_access_token[-4:]}" if len(new_access_token) >= 4 else "OAuth"
    credential.status = "valid"
    credential.status_detail = "Renovado automaticamente"
    credential.last_checked_at = datetime.datetime.utcnow()
    
    db.commit()
    db.refresh(credential)
    return credential
