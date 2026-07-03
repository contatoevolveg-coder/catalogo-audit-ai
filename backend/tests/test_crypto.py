import pytest
from cryptography.fernet import InvalidToken, Fernet
from backend.app.security.crypto import encrypt_secret, decrypt_secret, generate_masked_preview, get_fernet
from backend.app import config

@pytest.fixture(autouse=True)
def setup_encryption_key(monkeypatch):
    """Garante que uma chave Fernet válida está configurada por padrão nos testes."""
    # Gera uma chave Fernet válida
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", key)

def test_encrypt_decrypt_roundtrip():
    """Garante que a criptografia e a decriptografia de payloads funcionam corretamente."""
    payload = {"api_key": "12345-my-secret-key", "client_id": "bling-id"}
    token = encrypt_secret(payload)
    
    # Assert que não é salvo em texto limpo
    assert token != payload
    assert isinstance(token, str)
    
    decrypted = decrypt_secret(token)
    assert decrypted == payload
    assert decrypted["api_key"] == "12345-my-secret-key"

def test_decrypt_tampered_token_raises_invalid_token():
    """Garante que tokens adulterados/adulteração na string criptografada disparam InvalidToken."""
    payload = {"api_key": "sensitive-data"}
    token = encrypt_secret(payload)
    
    # Adultera o token alterando alguns caracteres do base64
    tampered_token = token[:-4] + "AAAA"
    
    with pytest.raises(InvalidToken):
        decrypt_secret(tampered_token)

def test_missing_or_malformed_key_raises_value_error(monkeypatch):
    """Garante que chaves de criptografia ausentes ou mal formadas disparam ValueError."""
    # Caso 1: Ausente (vazia)
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", "")
    with pytest.raises(ValueError) as exc:
        get_fernet()
    assert "não configurada" in str(exc.value)

    # Caso 2: Mal formada (não base64 ou de tamanho incorreto)
    monkeypatch.setattr(config, "CREDENTIAL_ENCRYPTION_KEY", "chave_pequena_invalida")
    with pytest.raises(ValueError) as exc2:
        get_fernet()
    assert "inválida ou mal formada" in str(exc2.value)

def test_generate_masked_preview():
    """Garante que o preview mascarado é gerado corretamente."""
    # Caso 1: access_token presente
    assert generate_masked_preview({"access_token": "APP_USR-12345678-abc"}) == "••••-abc"
    
    # Caso 2: api_key presente
    assert generate_masked_preview({"api_key": "my_super_secret_key"}) == "••••_key"
    
    # Caso 3: Fallback qualquer string
    assert generate_masked_preview({"other_field": "some_value"}) == "••••alue"
    
    # Caso 4: Vazio
    assert generate_masked_preview({}) == "••••"
