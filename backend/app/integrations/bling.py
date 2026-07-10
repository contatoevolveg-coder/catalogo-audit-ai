import time
import hmac
import hashlib
import httpx
import logging
import base64
from typing import Tuple, Any, Optional

logger = logging.getLogger(__name__)


def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str], client_secret: str) -> bool:
    """Valida a assinatura de um webhook do Bling (header X-Bling-Signature-256).

    Formato: "sha256=<hex>", HMAC-SHA256 do corpo bruto (bytes) da requisição,
    usando o Client Secret do app cadastrado no Bling Developers como chave.
    Comparação em tempo constante (hmac.compare_digest) para evitar timing attack.
    """
    if not signature_header or not client_secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    received_hash = signature_header.split("=", 1)[1].strip()
    expected_hash = hmac.new(
        client_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(received_hash, expected_hash)


def get_product_by_id(access_token: str, produto_id: int) -> Tuple[str, Any]:
    """Consulta o Bling ERP v3 para obter um produto pelo ID interno do Bling.

    GET /produtos/{id}
    Retorna (status, dados). Status: success | not_found | expired | error
    """
    url = f"https://api.bling.com.br/Api/v3/produtos/{produto_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    try:
        response = httpx.get(url, headers=headers, timeout=8.0)
        if response.status_code == 200:
            body = response.json()
            data = body.get("data")
            if data:
                return "success", data
            return "not_found", {"message": f"Produto com ID {produto_id} não encontrado"}
        elif response.status_code in [401, 403]:
            return "expired", {"message": "Token de acesso expirado ou inválido"}
        elif response.status_code == 404:
            return "not_found", {"message": f"Produto com ID {produto_id} não encontrado"}
        else:
            try:
                err_payload = response.json()
            except Exception:
                err_payload = {"detail": response.text}
            return "error", err_payload
    except httpx.RequestError as e:
        return "error", {"message": f"Erro de rede ao conectar com o Bling: {str(e)}"}
    except Exception as e:
        return "error", {"message": f"Erro inesperado: {str(e)}"}


def find_product_by_sku(access_token: str, sku: str) -> Tuple[str, Any]:
    """Consulta o Bling ERP v3 para localizar um produto pelo SKU (código).

    GET /produtos?codigo={sku}
    Implementa retry com backoff exponencial apenas para HTTP 429.
    Retorna uma tupla (status, detalhe/dados). Status: success | expired | not_found | error
    """
    url = "https://api.bling.com.br/Api/v3/produtos"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    params = {"codigo": sku}

    max_retries = 3
    initial_delay = 1.0
    backoff_factor = 2.0

    for attempt in range(max_retries):
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=10.0)
            status_code = response.status_code

            if status_code == 429:
                if attempt < max_retries - 1:
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(f"[Bling] Rate limit (429). Retrying in {delay}s...")
                    time.sleep(delay)
                    continue

            if status_code == 200:
                body = response.json()
                data = body.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    return "success", data[0]
                return "not_found", {"message": f"Nenhum produto encontrado com o SKU '{sku}'"}
            elif status_code in [401, 403]:
                return "expired", {"message": "Token de acesso expirado ou inválido", "status": status_code}
            else:
                try:
                    err_payload = response.json()
                except Exception:
                    err_payload = {"detail": response.text}
                return "error", err_payload

        except httpx.RequestError as e:
            return "error", {"message": f"Erro de rede ao conectar com o Bling: {str(e)}"}
        except Exception as e:
            return "error", {"message": f"Erro inesperado: {str(e)}"}

    return "error", {"message": "Limite de retentativas (429) excedido no Bling."}


def get_stock_quantity(access_token: str, produto_id: int) -> Tuple[str, Any]:
    """Consulta o saldo de estoque físico do produto no Bling ERP v3 pelo ID interno do produto.

    GET /estoques/saldos?idsProdutos[]={produto_id}
    Implementa retry com backoff exponencial apenas para HTTP 429.
    Retorna uma tupla (status, saldo_ou_erro). Status: success | expired | not_found | error
    """
    url = "https://api.bling.com.br/Api/v3/estoques/saldos"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    # Bling v3 aceita idsProdutos[] como query param para array
    params = {"idsProdutos[]": produto_id}

    max_retries = 3
    initial_delay = 1.0
    backoff_factor = 2.0

    for attempt in range(max_retries):
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=10.0)
            status_code = response.status_code

            if status_code == 429:
                if attempt < max_retries - 1:
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(f"[Bling] Rate limit (429). Retrying in {delay}s...")
                    time.sleep(delay)
                    continue

            if status_code == 200:
                body = response.json()
                data = body.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    item = data[0]
                    # Extrai o saldo físico. Se não existir, faz fallback para saldo virtual ou 0
                    saldo = item.get("saldoFisico", item.get("saldoVirtual", 0))
                    try:
                        return "success", int(saldo)
                    except (ValueError, TypeError):
                        return "success", 0
                return "not_found", {"message": "Saldo de estoque não encontrado para o ID fornecido"}
            elif status_code in [401, 403]:
                return "expired", {"message": "Token de acesso expirado ou inválido", "status": status_code}
            else:
                try:
                    err_payload = response.json()
                except Exception:
                    err_payload = {"detail": response.text}
                return "error", err_payload

        except httpx.RequestError as e:
            return "error", {"message": f"Erro de rede ao conectar com o Bling: {str(e)}"}
        except Exception as e:
            return "error", {"message": f"Erro inesperado: {str(e)}"}

    return "error", {"message": "Limite de retentativas (429) excedido no Bling."}


def build_authorization_url(client_id: str, state: str) -> str:
    """Constrói a URL de autorização OAuth2 para o Bling."""
    return f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={client_id}&state={state}"


def _get_bling_token(client_id: str, client_secret: str, data: dict) -> Tuple[bool, dict]:
    """Helper para obter tokens (seja por authorization_code ou refresh_token)."""
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    
    # Autenticação via Basic header no Bling
    auth_str = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "1.0", # Bling exige 1.0 (ou json dependendo da doc, 1.0 no cabeçalho Accept é comum na doc deles, ou application/json)
        "Authorization": f"Basic {b64_auth}"
    }

    try:
        response = httpx.post(url, headers=headers, data=data, timeout=10.0)
        body = response.json()
        return (response.status_code == 200, body)
    except Exception as e:
        return (False, {"error": str(e)})


def exchange_code_for_token(client_id: str, client_secret: str, code: str) -> Tuple[bool, dict]:
    """Troca o authorization code por tokens no Bling."""
    data = {
        "grant_type": "authorization_code",
        "code": code
    }
    return _get_bling_token(client_id, client_secret, data)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Tuple[bool, dict]:
    """Renova o access_token usando o refresh_token no Bling."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    return _get_bling_token(client_id, client_secret, data)
