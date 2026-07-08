import time
import hmac
import hashlib
import httpx
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from backend.app.database import ExternalCallLog

def generate_signature(partner_key: str, payload_str: str) -> str:
    """Gera a assinatura HMAC-SHA256 exigida pela Shopee Open Platform V2."""
    return hmac.new(
        partner_key.encode('utf-8'),
        payload_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def build_authorization_url(partner_id: str, partner_key: str, redirect_uri: str) -> str:
    """Constrói a URL de autorização OAuth2 para a Shopee."""
    timestamp = int(time.time())
    path = "/api/v2/shop/auth_partner"
    payload = f"{partner_id}{path}{timestamp}"
    sign = generate_signature(partner_key, payload)
    
    # Adicionamos redirect_uri para Shopee, que retorna o código e o shop_id para lá
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}&redirect={redirect_uri}"
    return url

def exchange_code_for_token(partner_id: int, partner_key: str, code: str, shop_id: int) -> Tuple[bool, dict]:
    """Troca o authorization code por tokens na Shopee."""
    timestamp = int(time.time())
    path = "/api/v2/auth/token/get"
    payload = f"{partner_id}{path}{timestamp}"
    sign = generate_signature(partner_key, payload)
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
    body = {
        "code": code,
        "shop_id": shop_id,
        "partner_id": partner_id
    }
    try:
        response = httpx.post(url, json=body, timeout=10.0)
        body_resp = response.json()
        if body_resp.get("error"):
            return (False, body_resp)
        return (response.status_code == 200, body_resp)
    except Exception as e:
        return (False, {"error": str(e)})

def refresh_access_token(partner_id: int, partner_key: str, refresh_token: str, shop_id: int) -> Tuple[bool, dict]:
    """Renova o access_token usando o refresh_token."""
    timestamp = int(time.time())
    path = "/api/v2/auth/access_token/get"
    payload = f"{partner_id}{path}{timestamp}"
    sign = generate_signature(partner_key, payload)
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
    body = {
        "refresh_token": refresh_token,
        "shop_id": shop_id,
        "partner_id": partner_id
    }
    try:
        response = httpx.post(url, json=body, timeout=10.0)
        body_resp = response.json()
        if body_resp.get("error"):
            return (False, body_resp)
        return (response.status_code == 200, body_resp)
    except Exception as e:
        return (False, {"error": str(e)})

def check_credential_shopee(secret_payload: dict, partner_id: int, partner_key: str, db: Session) -> Tuple[str, Optional[str]]:
    """Valida a conectividade das credenciais da Shopee."""
    token = secret_payload.get("access_token")
    shop_id = secret_payload.get("shop_id")
    
    if not token or not shop_id:
        return "error", "Chave 'access_token' ou 'shop_id' ausente."
        
    timestamp = int(time.time())
    path = "/api/v2/shop/get_shop_info"
    payload = f"{partner_id}{path}{timestamp}{token}{shop_id}"
    sign = generate_signature(partner_key, payload)
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&access_token={token}&shop_id={shop_id}&sign={sign}"
    
    start_time = time.time()
    success = False
    status_code = None
    status = "untested"
    status_detail = None
    
    try:
        response = httpx.get(url, timeout=5.0)
        status_code = response.status_code
        data = response.json()
        
        if data.get("error"):
            if data["error"] in ["error_auth", "error_token_expired"]:
                status = "expired"
                status_detail = "Token inválido ou expirado"
            else:
                status = "error"
                status_detail = data.get("message", f"Erro da Shopee: {data['error']}")
        else:
            status = "valid"
            status_detail = None
            success = True
            
    except httpx.RequestError as e:
        status = "error"
        status_detail = f"Erro de rede: {str(e)}"
    except Exception as e:
        status = "error"
        status_detail = f"Erro inesperado: {str(e)}"
        
    latency = time.time() - start_time
    
    log_detail = {
        "status_code": status_code,
        "status": status,
        "status_detail": status_detail
    }
    
    db_log = ExternalCallLog(
        kind="credential_check",
        target_url="https://partner.shopeemobile.com/api/v2/shop/get_shop_info",
        status_code=status_code,
        success=success,
        latency_seconds=latency,
        detail=log_detail
    )
    db.add(db_log)
    db.commit()
    
    return status, status_detail

def publish_item(partner_id: int, partner_key: str, access_token: str, shop_id: int, request_payload: dict) -> Tuple[bool, dict]:
    """Publica um anúncio na Shopee utilizando a API POST /api/v2/product/add_item."""
    timestamp = int(time.time())
    path = "/api/v2/product/add_item"
    payload = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    sign = generate_signature(partner_key, payload)
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&access_token={access_token}&shop_id={shop_id}&sign={sign}"
    
    try:
        response = httpx.post(url, json=request_payload, timeout=10.0)
        status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"detail": response.text}
            
        if body.get("error"):
            return (False, body)
            
        return (status_code == 200, body)
    except httpx.RequestError as e:
        return (False, {"error": f"Erro de rede: {str(e)}"})
    except Exception as e:
        return (False, {"error": f"Erro inesperado: {str(e)}"})
