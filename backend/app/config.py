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
