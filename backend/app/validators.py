from typing import Dict, List, Any, Tuple
import time
import httpx
import pandas as pd
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from backend.app.database import ExternalCallLog
from backend.app.constants import PROHIBITED_PROMOTIONAL_TERMS

def is_empty(val: Any) -> bool:
    """Verifica se um valor está vazio (None, NaN, string vazia ou lista vazia)."""
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if isinstance(val, str) and not val.strip():
        return True
    if isinstance(val, list) and not val:
        return True
    return False

def validate_image_url(url: str, db: Session) -> Tuple[bool, str]:
    """Valida se uma URL de imagem é válida, bem-formada e acessível.

    Faz uma requisição HTTP GET rápida (com timeout de 5s), verifica se o status é 200
    e se o Content-Type é de imagem (image/*). Registra a chamada na tabela ExternalCallLog.
    """
    if is_empty(url):
        return False, "URL nula ou vazia"

    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "URL malformada (faltando protocolo ou domínio)"
    except Exception as e:
        return False, f"URL malformada: {str(e)}"

    start_time = time.time()
    success = False
    status_code = None
    detail = {}
    error_msg = ""

    try:
        # Realiza chamada HTTP GET com timeout de 5 segundos
        response = httpx.get(url, follow_redirects=True, timeout=5.0)
        status_code = response.status_code
        content_type = response.headers.get("content-type", "")

        if status_code == 200:
            if content_type.lower().startswith("image/"):
                success = True
            else:
                error_msg = f"Content-Type inválido: {content_type} (esperado image/*)"
        else:
            error_msg = f"Erro HTTP: status {status_code}"
    except httpx.TimeoutException:
        error_msg = "Timeout excedido ao carregar imagem (~5s)"
    except Exception as e:
        error_msg = f"Erro de rede: {str(e)}"

    latency = time.time() - start_time
    detail["error_message"] = error_msg

    # Salva no log de chamadas externas
    call_log = ExternalCallLog(
        kind="image_check",
        target_url=url,
        status_code=status_code,
        success=success,
        latency_seconds=latency,
        detail=detail
    )
    db.add(call_log)
    db.commit()

    return success, error_msg

def validate_row_ml(row_data: Dict[str, Any], db: Session) -> List[Dict[str, Any]]:
    """Valida uma linha mapeada contra as regras específicas do Mercado Livre.

    Retorna uma lista de dicionários contendo os erros/avisos no formato:
    [{'field': str, 'code': str, 'message': str, 'severity': 'error'|'warning'}]
    """
    errors = []

    # 1. Campos obrigatórios
    required_fields = ["title", "category", "price", "available_quantity", "condition", "images"]
    for field in required_fields:
        val = row_data.get(field)
        if is_empty(val):
            errors.append({
                "field": field,
                "code": "missing_required",
                "message": f"O campo obrigatório '{field}' está ausente ou vazio.",
                "severity": "error"
            })

    # Título (limite 60 caracteres, sem termos proibidos)
    title = row_data.get("title")
    if not is_empty(title):
        title_str = str(title).strip()
        if len(title_str) > 60:
            errors.append({
                "field": "title",
                "code": "title_too_long",
                "message": f"O título excede o limite de 60 caracteres (atual: {len(title_str)}).",
                "severity": "error"
            })
        
        title_lower = title_str.lower()
        found_terms = [term for term in PROHIBITED_PROMOTIONAL_TERMS if term in title_lower]
        if found_terms:
            errors.append({
                "field": "title",
                "code": "promotional_terms",
                "message": f"O título contém termos promocionais proibidos: {', '.join(found_terms)}.",
                "severity": "error"
            })

    # Preço (deve ser maior que zero)
    price_val = row_data.get("price")
    if not is_empty(price_val):
        try:
            price_float = float(price_val)
            if price_float <= 0:
                errors.append({
                    "field": "price",
                    "code": "invalid_price",
                    "message": "O preço deve ser maior que zero.",
                    "severity": "error"
                })
        except (ValueError, TypeError):
            errors.append({
                "field": "price",
                "code": "invalid_type",
                "message": f"O valor de preço é inválido: {price_val}.",
                "severity": "error"
            })

    # Estoque / Quantidade disponível (deve ser inteiro >= 1)
    qty_val = row_data.get("available_quantity")
    if not is_empty(qty_val):
        try:
            qty_float = float(qty_val)
            # Verifica se é inteiro e maior ou igual a 1
            if not qty_float.is_integer() or qty_float < 1:
                errors.append({
                    "field": "available_quantity",
                    "code": "invalid_quantity",
                    "message": "A quantidade disponível de estoque deve ser um número inteiro igual ou maior que 1.",
                    "severity": "error"
                })
        except (ValueError, TypeError):
            errors.append({
                "field": "available_quantity",
                "code": "invalid_type",
                "message": f"O valor do estoque é inválido: {qty_val}.",
                "severity": "error"
            })

    # Condição (new ou used)
    cond_val = row_data.get("condition")
    if not is_empty(cond_val):
        cond_str = str(cond_val).strip().lower()
        if cond_str not in ["new", "used", "novo", "usado"]:
            errors.append({
                "field": "condition",
                "code": "invalid_condition",
                "message": f"A condição do produto deve ser 'new' (novo) ou 'used' (usado). Recebido: {cond_val}.",
                "severity": "error"
            })

    # Imagens (mínimo 1 URL válida de imagem)
    images = row_data.get("images")
    if not is_empty(images):
        urls = []
        if isinstance(images, list):
            urls = [u for u in images if isinstance(u, str) and u.strip()]
        elif isinstance(images, str):
            urls = [u.strip() for u in images.split(",") if u.strip()]

        if not urls:
            errors.append({
                "field": "images",
                "code": "no_images",
                "message": "O produto não possui URLs de imagem informadas.",
                "severity": "error"
            })
        else:
            valid_images_count = 0
            for url in urls:
                is_valid, reason = validate_image_url(url, db)
                if is_valid:
                    valid_images_count += 1
                else:
                    errors.append({
                        "field": "images",
                        "code": "invalid_image_url",
                        "message": f"URL de imagem inválida/inacessível: {reason} ({url}).",
                        "severity": "error"
                    })
            if valid_images_count == 0:
                errors.append({
                    "field": "images",
                    "code": "no_valid_images",
                    "message": "O produto precisa ter pelo menos 1 imagem válida acessível.",
                    "severity": "error"
                })

    # GTIN/EAN (opcional, se presente numérico de 8 a 14 dígitos)
    gtin_val = row_data.get("gtin_ean")
    if not is_empty(gtin_val):
        gtin_str = str(gtin_val).strip()
        if gtin_str.endswith(".0"):
            gtin_str = gtin_str[:-2]
        if gtin_str:  # se não estiver vazio
            if not gtin_str.isdigit() or not (8 <= len(gtin_str) <= 14):
                errors.append({
                    "field": "gtin_ean",
                    "code": "invalid_gtin_ean",
                    "message": f"O GTIN/EAN deve conter apenas dígitos e ter de 8 a 14 algarismos. Recebido: {gtin_str}.",
                    "severity": "error"
                })

    # 2. AVISOS (Warnings - não bloqueiam a importação)
    brand = row_data.get("brand")
    if is_empty(brand):
        errors.append({
            "field": "brand",
            "code": "missing_brand_warning",
            "message": "Falta de marca (Recomendado). A ausência pode afetar o SEO na busca do Mercado Livre.",
            "severity": "warning"
        })

    model = row_data.get("model")
    if is_empty(model):
        errors.append({
            "field": "model",
            "code": "missing_model_warning",
            "message": "Falta de modelo (Recomendado). Ajuda na especificação técnica do produto.",
            "severity": "warning"
        })

    description = row_data.get("description")
    if is_empty(description) or len(str(description).strip()) < 50:
        length = len(str(description).strip()) if not is_empty(description) else 0
        errors.append({
            "field": "description",
            "code": "short_description_warning",
            "message": f"A descrição está muito curta (recomendado mínimo 50 caracteres, atual: {length}).",
            "severity": "warning"
        })

    return errors
