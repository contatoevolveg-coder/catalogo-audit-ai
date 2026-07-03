import time
import httpx
from typing import Tuple, Optional, List, Dict
from sqlalchemy.orm import Session
from backend.app.database import ExternalCallLog

def check_credential_ml(secret_payload: dict, db: Session) -> Tuple[str, Optional[str]]:
    """Valida a conectividade das credenciais do Mercado Livre na API oficial.

    Realiza uma chamada HTTP GET a https://api.mercadolibre.com/users/me com
    o access_token. Registra a chamada na tabela ExternalCallLog sem expor
    o segredo.
    """
    token = secret_payload.get("access_token")
    if not token:
        return "error", "Chave 'access_token' ausente no payload do segredo."

    url = "https://api.mercadolibre.com/users/me"
    headers = {"Authorization": f"Bearer {token}"}
    
    start_time = time.time()
    success = False
    status_code = None
    status = "untested"
    status_detail = None

    try:
        response = httpx.get(url, headers=headers, timeout=5.0)
        status_code = response.status_code
        if status_code == 200:
            status = "valid"
            status_detail = None
            success = True
        elif status_code in [401, 403]:
            status = "expired"
            status_detail = "Token inválido ou expirado"
        else:
            status = "error"
            status_detail = f"HTTP {status_code}"
    except httpx.RequestError as e:
        status = "error"
        status_detail = f"Erro de rede: {str(e)}"
    except Exception as e:
        status = "error"
        status_detail = f"Erro inesperado: {str(e)}"

    latency = time.time() - start_time

    # Detalhe do log de auditoria - NUNCA expor o token bruto aqui!
    log_detail = {
        "status_code": status_code,
        "status": status,
        "status_detail": status_detail
    }

    db_log = ExternalCallLog(
        kind="credential_check",
        target_url="https://api.mercadolibre.com/users/me",
        status_code=status_code,
        success=success,
        latency_seconds=latency,
        detail=log_detail
    )
    db.add(db_log)
    db.commit()

    return status, status_detail


def predict_category(title: str) -> List[Dict[str, str]]:
    """Sugere categorias do Mercado Livre com base no título do anúncio.

    Chama o endpoint público (sem autenticação) GET /sites/MLB/category_predictor/predict.
    Retorna uma lista de dicionários contendo category_id e category_name.
    """
    url = "https://api.mercadolibre.com/sites/MLB/category_predictor/predict"
    params = {"title": title}
    try:
        response = httpx.get(url, params=params, timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                # O retorno é do tipo: [{"id": "MLB...", "name": "..."}, ...]
                return [{"category_id": item["id"], "category_name": item["name"]} for item in data if "id" in item and "name" in item]
            elif isinstance(data, dict):
                # Às vezes o ML pode retornar um dicionário único
                return [{"category_id": data["id"], "category_name": data["name"]}] if "id" in data and "name" in data else []
        return []
    except Exception:
        # Erro de rede ou de parse não impede o fluxo; retorna lista vazia
        return []


def publish_item(access_token: str, payload: dict) -> Tuple[bool, dict]:
    """Publica um anúncio no Mercado Livre utilizando a API POST /items.

    Realiza retentativas com backoff exponencial apenas em caso de HTTP 429.
    Retorna uma tupla (sucesso: bool, corpo_resposta: dict).
    """
    url = "https://api.mercadolibre.com/items"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    max_retries = 3
    initial_delay = 1.0
    backoff_factor = 2.0

    for attempt in range(max_retries):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=10.0)
            status_code = response.status_code

            # Caso seja Rate Limit (429), aplica backoff
            if status_code == 429:
                if attempt < max_retries - 1:
                    delay = initial_delay * (backoff_factor ** attempt)
                    time.sleep(delay)
                    continue

            try:
                body = response.json()
            except Exception:
                body = {"detail": response.text}

            return (status_code == 201, body)

        except httpx.RequestError as e:
            return (False, {"error": f"Erro de rede: {str(e)}"})
        except Exception as e:
            return (False, {"error": f"Erro inesperado: {str(e)}"})

    return (False, {"error": "Limite de retentativas (429) excedido."})


def publish_item_description(access_token: str, item_id: str, description_text: str) -> Tuple[bool, dict]:
    """Cadastra a descrição de um item criado no Mercado Livre.

    POST /items/{item_id}/description
    """
    url = f"https://api.mercadolibre.com/items/{item_id}/description"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"plain_text": description_text}

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"detail": response.text}

        return (status_code in [200, 201], body)
    except Exception as e:
        return (False, {"error": f"Erro inesperado ao enviar descrição: {str(e)}"})

