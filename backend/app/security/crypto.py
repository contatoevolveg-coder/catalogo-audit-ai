import json
from cryptography.fernet import Fernet
from backend.app import config

def get_fernet() -> Fernet:
    """Instancia e retorna o cliente Fernet utilizando a chave CREDENTIAL_ENCRYPTION_KEY.

    Dispara ValueError se a chave estiver ausente ou for inválida/mal formada.
    """
    if not config.is_encryption_configured():
        raise ValueError(
            "Chave de criptografia de credenciais não configurada! Defina a variável "
            "CREDENTIAL_ENCRYPTION_KEY no arquivo .env (ou gere uma nova chave)."
        )
    
    key_str = config.CREDENTIAL_ENCRYPTION_KEY.strip()
    try:
        # Fernet exige uma chave simétrica de 32 bytes codificada em base64 url-safe
        return Fernet(key_str.encode("utf-8"))
    except Exception as e:
        raise ValueError(
            f"Chave CREDENTIAL_ENCRYPTION_KEY inválida ou mal formada: {str(e)}"
        )

def encrypt_secret(payload: dict) -> str:
    """Criptografa o payload do segredo bruto (dicionário) e retorna o token Fernet como string."""
    fernet = get_fernet()
    # Serializa o dict para JSON string antes de criptografar
    serialized = json.dumps(payload).encode("utf-8")
    encrypted_bytes = fernet.encrypt(serialized)
    return encrypted_bytes.decode("utf-8")

def decrypt_secret(token_str: str) -> dict:
    """Decriptografa o token Fernet string e retorna o payload do segredo original como dicionário."""
    fernet = get_fernet()
    decrypted_bytes = fernet.decrypt(token_str.encode("utf-8"))
    return json.loads(decrypted_bytes.decode("utf-8"))

def generate_masked_preview(payload: dict) -> str:
    """Gera uma pré-visualização segura mascarando todos os caracteres exceto os últimos 4 do token principal.

    Exemplo: "••••7f3a"
    """
    # Procura por chaves de segredos comuns
    common_keys = ["access_token", "token", "api_key", "client_secret", "secret", "password"]
    for key in common_keys:
        if key in payload and isinstance(payload[key], str) and payload[key].strip():
            val = payload[key].strip()
            last_chars = val[-4:] if len(val) >= 4 else val
            return f"••••{last_chars}"
            
    # Fallback: utiliza qualquer primeira string que encontrar no payload
    for k, v in payload.items():
        if isinstance(v, str) and v.strip():
            val = v.strip()
            last_chars = val[-4:] if len(val) >= 4 else val
            return f"••••{last_chars}"
            
    return "••••"
