import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional
from backend.app import config

def get_jwt_secret() -> str:
    """Retorna a chave secreta do JWT ou lança ValueError se não configurada."""
    if not config.is_jwt_configured():
        raise ValueError(
            "Chave secreta JWT não configurada! Defina a variável JWT_SECRET_KEY no arquivo .env."
        )
    return config.JWT_SECRET_KEY

def hash_password(password: str) -> str:
    """Gera o hash da senha usando bcrypt de forma direta e segura."""
    # Gera um salt e computa o hash
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    """Verifica se a senha em texto plano bate com o hash armazenado."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_access_token(username: str) -> str:
    """Gera um token JWT com algoritmo HS256 assinado com a JWT_SECRET_KEY."""
    secret = get_jwt_secret()
    # Expiração obtida das configurações
    expire_hours = config.JWT_EXPIRE_HOURS
    
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def decode_access_token(token: str) -> Optional[dict]:
    """Decodifica e valida o token JWT.
    
    Retorna o payload se válido ou None se expirado ou corrompido.
    """
    secret = get_jwt_secret()
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
