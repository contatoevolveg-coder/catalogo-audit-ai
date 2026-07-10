from typing import Tuple, Optional, Dict, Callable
from sqlalchemy.orm import Session
from backend.app.integrations.mercado_livre import check_credential_ml
from backend.app.integrations.shopee import check_credential_shopee
from backend.app.integrations.bling import check_credential_bling
from backend.app.config import SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY

# Mapeamento dinâmico de verificadores de credenciais por provedor
CHECKERS: Dict[str, Callable[[dict, Session], Tuple[str, Optional[str]]]] = {
    "mercado_livre": check_credential_ml,
    "bling": check_credential_bling,
}


def _check_shopee_adapter(secret_payload: dict, db: Session) -> Tuple[str, Optional[str]]:
    """Adapta check_credential_shopee (que exige partner_id/key) à assinatura padrão dos checkers."""
    if not SHOPEE_PARTNER_ID or not SHOPEE_PARTNER_KEY:
        return "error", "SHOPEE_PARTNER_ID/SHOPEE_PARTNER_KEY não configurados no ambiente."
    return check_credential_shopee(secret_payload, int(SHOPEE_PARTNER_ID), SHOPEE_PARTNER_KEY, db)


CHECKERS["shopee"] = _check_shopee_adapter


def check_credential(provider: str, secret_payload: dict, db: Session) -> Tuple[str, Optional[str]]:
    """Roteia a validação de conectividade para o provedor correspondente.

    Se não houver checker cadastrado para o provedor, retorna status 'untested'
    e mensagem explicativa (fallback genérico plugável).
    """
    checker = CHECKERS.get(provider)
    if checker:
        return checker(secret_payload, db)
    return "untested", "Verificação não implementada para este provedor ainda"
