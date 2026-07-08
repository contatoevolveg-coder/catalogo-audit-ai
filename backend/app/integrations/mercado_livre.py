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


def fetch_unanswered_questions(access_token: str) -> List[dict]:
    """Busca as perguntas não respondidas do vendedor logado.

    GET /my/received_questions/search?status=UNANSWERED
    Retorna a lista de dicionários das perguntas cruas obtidas.
    Aplica retry com backoff exponencial para HTTP 429.
    """
    url = "https://api.mercadolibre.com/my/received_questions/search"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    params = {"status": "UNANSWERED"}

    max_retries = 3
    initial_delay = 1.0
    backoff_factor = 2.0

    for attempt in range(max_retries):
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=10.0)
            status_code = response.status_code

            # Caso seja Rate Limit (429), aplica backoff
            if status_code == 429:
                if attempt < max_retries - 1:
                    delay = initial_delay * (backoff_factor ** attempt)
                    time.sleep(delay)
                    continue

            if status_code == 200:
                data = response.json()
                return data.get("questions", []) if isinstance(data, dict) else []
            return []

        except Exception:
            if attempt == max_retries - 1:
                return []
            time.sleep(initial_delay * (backoff_factor ** attempt))

    return []


def submit_answer(access_token: str, question_id: str, text: str) -> Tuple[bool, dict]:
    """Envia uma resposta para uma pergunta específica no Mercado Livre.

    POST /answers
    Retorna (sucesso: bool, corpo_resposta: dict).
    """
    url = "https://api.mercadolibre.com/answers"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    try:
        q_id = int(question_id)
    except ValueError:
        q_id = question_id

    payload = {
        "question_id": q_id,
        "text": text
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        status_code = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"detail": response.text}

        return (status_code in [200, 201], body)
    except Exception as e:
        return (False, {"error": f"Erro inesperado ao enviar resposta: {str(e)}"})


def build_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Constrói a URL de autorização OAuth2 para o Mercado Livre."""
    return f"https://auth.mercadolivre.com.br/authorization?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&state={state}"


def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Tuple[bool, dict]:
    """Troca o authorization code por tokens no Mercado Livre."""
    url = "https://api.mercadolibre.com/oauth/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri
    }
    
    try:
        response = httpx.post(url, headers=headers, data=data, timeout=10.0)
        body = response.json()
        return (response.status_code == 200, body)
    except Exception as e:
        return (False, {"error": str(e)})


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Tuple[bool, dict]:
    """Renova o access_token usando o refresh_token."""
    url = "https://api.mercadolibre.com/oauth/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    try:
        response = httpx.post(url, headers=headers, data=data, timeout=10.0)
        body = response.json()
        return (response.status_code == 200, body)
    except Exception as e:
        return (False, {"error": str(e)})


def get_seller_id(access_token: str) -> Optional[str]:
    """Obtém o ID do usuário (seller) logado."""
    url = "https://api.mercadolibre.com/users/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = httpx.get(url, headers=headers, timeout=5.0)
        if response.status_code == 200:
            return str(response.json().get("id"))
    except Exception:
        pass
    return None

def fetch_orders(access_token: str, seller_id: str, limit: int = 50, offset: int = 0) -> List[dict]:
    """Busca as vendas recentes do vendedor."""
    url = "https://api.mercadolibre.com/orders/search"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "seller": seller_id,
        "limit": limit,
        "offset": offset,
        "sort": "date_desc"
    }

    try:
        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("results", [])
    except Exception:
        pass
    return []


def fetch_seller_item_ids(access_token: str, seller_id: str, limit: int = 50, offset: int = 0) -> Tuple[List[str], int]:
    """Busca os IDs dos anúncios ativos do vendedor logado.

    GET /users/{seller_id}/items/search
    Retorna (lista_de_item_ids, total_de_itens_no_vendedor).
    """
    url = f"https://api.mercadolibre.com/users/{seller_id}/items/search"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": limit, "offset": offset}

    try:
        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            ids = data.get("results", [])
            total = data.get("paging", {}).get("total", len(ids))
            return ids, total
    except Exception:
        pass
    return [], 0


def fetch_items_details(access_token: str, item_ids: List[str]) -> List[dict]:
    """Busca os dados completos de até 20 itens por chamada (multiget da API do ML).

    GET /items?ids=id1,id2,...
    Retorna a lista de corpos (body) dos itens que responderam com sucesso (code 200).
    """
    if not item_ids:
        return []

    url = "https://api.mercadolibre.com/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    results = []

    # A API do ML aceita no máximo 20 IDs por chamada no endpoint multiget.
    for i in range(0, len(item_ids), 20):
        batch = item_ids[i:i + 20]
        params = {"ids": ",".join(batch)}
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                for entry in response.json():
                    if entry.get("code") == 200 and entry.get("body"):
                        results.append(entry["body"])
        except Exception:
            continue

    return results


def fetch_item_description(access_token: str, item_id: str) -> str:
    """Busca a descrição em texto puro de um item já publicado.

    GET /items/{item_id}/description
    """
    url = f"https://api.mercadolibre.com/items/{item_id}/description"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("plain_text") or data.get("text") or ""
    except Exception:
        pass
    return ""
