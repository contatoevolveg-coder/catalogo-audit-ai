from typing import Tuple, Optional, Dict, Callable
from sqlalchemy.orm import Session
from backend.app.integrations.mercado_livre import check_credential_ml

# Mapeamento dinâmico de verificadores de credenciais por provedor
CHECKERS: Dict[str, Callable[[dict, Session], Tuple[str, Optional[str]]]] = {
    "mercado_livre": check_credential_ml,
}

def check_credential(provider: str, secret_payload: dict, db: Session) -> Tuple[str, Optional[str]]:
    """Roteia a validação de conectividade para o provedor correspondente.

    Se não houver checker cadastrado para o provedor, retorna status 'untested'
    e mensagem explicativa (fallback genérico plugável).
    """
    checker = CHECKERS.get(provider)
    if checker:
        return checker(secret_payload, db)
    return "untested", "Verificação não implementada para este provedor ainda"
