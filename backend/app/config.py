"""Configuração central da aplicação, carregada a partir de variáveis de ambiente.

Toda leitura de ambiente acontece aqui (um único `load_dotenv`), evitando que
cada módulo carregue o `.env` por conta própria.
"""
import os
from dotenv import load_dotenv

# Diretório base do backend (.../backend)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Valor sentinela usado para rodar a aplicação sem chamar a API real.
MOCK_KEY = "mock"

# Chaves que indicam que a API ainda não foi configurada de fato.
_PLACEHOLDER_KEYS = {"", "sua_chave_aqui"}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "sua_chave_aqui")
# Default para um modelo válido do Google AI Studio. Ajuste conforme os
# modelos disponíveis na sua conta (ex.: gemini-2.5-flash, gemini-2.0-flash).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DATABASE_URL = os.getenv("DATABASE_URL")

# Origens permitidas para CORS. Lista separada por vírgula no .env.
# Default: portas locais do Streamlit. NÃO use "*" com credenciais em produção.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501"
    ).split(",")
    if origin.strip()
]


def is_mock_mode() -> bool:
    """Retorna True quando a aplicação deve usar respostas simuladas."""
    return GEMINI_API_KEY == MOCK_KEY


def is_api_key_configured() -> bool:
    """Retorna True se houver uma chave de API real configurada."""
    return GEMINI_API_KEY not in _PLACEHOLDER_KEYS and not is_mock_mode()


# Configurações de segurança para credenciais e área administrativa
CREDENTIAL_ENCRYPTION_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# Configurações de Rate Limiting para Login / Registro
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

# Configurações de Integração OAuth2 (Mercado Livre, Bling, Shopee)
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
BLING_CLIENT_ID = os.getenv("BLING_CLIENT_ID", "")
BLING_CLIENT_SECRET = os.getenv("BLING_CLIENT_SECRET", "")
SHOPEE_PARTNER_ID = os.getenv("SHOPEE_PARTNER_ID", "")
SHOPEE_PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY", "")
OAUTH_REDIRECT_BASE_URL = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://localhost:8000")


def is_admin_key_configured() -> bool:
    """Retorna True se a chave de administrador (ADMIN_API_KEY) estiver configurada."""
    return bool(ADMIN_API_KEY.strip())


def is_encryption_configured() -> bool:
    """Retorna True se a chave de criptografia de credenciais estiver configurada."""
    return bool(CREDENTIAL_ENCRYPTION_KEY.strip())


def is_jwt_configured() -> bool:
    """Retorna True se a chave secreta do JWT (JWT_SECRET_KEY) estiver configurada."""
    return bool(JWT_SECRET_KEY.strip())


# Configurações do Scheduler (intervalos em minutos)
SCHEDULER_SYNC_STOCK_MINUTES = int(os.getenv("SCHEDULER_SYNC_STOCK_MINUTES", "15"))
SCHEDULER_SYNC_QUESTIONS_MINUTES = int(os.getenv("SCHEDULER_SYNC_QUESTIONS_MINUTES", "10"))
SCHEDULER_REFRESH_TOKENS_MINUTES = int(os.getenv("SCHEDULER_REFRESH_TOKENS_MINUTES", "30"))
SCHEDULER_CHECK_ALERTS_MINUTES = int(os.getenv("SCHEDULER_CHECK_ALERTS_MINUTES", "10"))



