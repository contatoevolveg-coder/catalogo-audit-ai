import pytest
import hmac
import hashlib
from backend.app.integrations.shopee import generate_signature, exchange_code_for_token, publish_item

def test_generate_signature():
    partner_key = "test_key"
    payload = "test_payload"
    expected = hmac.new(
        partner_key.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    assert generate_signature(partner_key, payload) == expected

def test_exchange_code_for_token_success(monkeypatch):
    class MockResponse:
        status_code = 200
        def json(self):
            return {"access_token": "token123", "refresh_token": "refresh123", "expire_in": 10000}
            
    def mock_post(*args, **kwargs):
        return MockResponse()
        
    monkeypatch.setattr("httpx.post", mock_post)
    success, body = exchange_code_for_token(123, "key", "code123", 456)
    assert success is True
    assert body["access_token"] == "token123"

def test_exchange_code_for_token_error(monkeypatch):
    class MockResponse:
        status_code = 200
        def json(self):
            return {"error": "error_auth", "message": "Invalid code"}
            
    def mock_post(*args, **kwargs):
        return MockResponse()
        
    monkeypatch.setattr("httpx.post", mock_post)
    success, body = exchange_code_for_token(123, "key", "code123", 456)
    assert success is False
    assert body["error"] == "error_auth"

def test_publish_item_success(monkeypatch):
    class MockResponse:
        status_code = 200
        def json(self):
            return {"response": {"item_id": 999}}
            
    def mock_post(*args, **kwargs):
        return MockResponse()
        
    monkeypatch.setattr("httpx.post", mock_post)
    success, body = publish_item(123, "key", "token123", 456, {"item_name": "Test"})
    assert success is True
    assert body["response"]["item_id"] == 999

def test_publish_item_error(monkeypatch):
    class MockResponse:
        status_code = 200
        def json(self):
            return {"error": "error_param", "message": "Invalid category"}
            
    def mock_post(*args, **kwargs):
        return MockResponse()
        
    monkeypatch.setattr("httpx.post", mock_post)
    success, body = publish_item(123, "key", "token123", 456, {"item_name": "Test"})
    assert success is False
    assert body["error"] == "error_param"
