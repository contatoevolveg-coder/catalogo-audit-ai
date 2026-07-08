from unittest.mock import MagicMock, patch
import pytest
import httpx
from google.genai import types

from backend.app.agent import download_image, audit_product_with_gemini
from backend.app.schemas import GeminiAuditResponse

def test_download_image_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {
        "content-type": "image/png",
        "content-length": "100"
    }
    mock_response.iter_bytes.return_value = [b"chunk1", b"chunk2"]

    # Mock the stream method
    class MockContextManager:
        def __enter__(self):
            return mock_response
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.stream", return_value=MockContextManager()):
        data, content_type = download_image("https://example.com/test.png")
        assert data == b"chunk1chunk2"
        assert content_type == "image/png"

def test_download_image_size_limit():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {
        "content-type": "image/png",
        "content-length": str(5 * 1024 * 1024) # 5MB
    }

    class MockContextManager:
        def __enter__(self):
            return mock_response
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.stream", return_value=MockContextManager()):
        with pytest.raises(ValueError, match="Imagem excede tamanho máximo permitido"):
            download_image("https://example.com/large.png")

def test_download_image_invalid_content_type():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {
        "content-type": "text/html",
    }

    class MockContextManager:
        def __enter__(self):
            return mock_response
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.stream", return_value=MockContextManager()):
        with pytest.raises(ValueError, match="Content-type inválido para imagem"):
            download_image("https://example.com/page.html")

@patch("backend.app.agent.is_mock_mode", return_value=False)
@patch("backend.app.agent.is_api_key_configured", return_value=True)
@patch("backend.app.agent.get_gemini_client")
@patch("backend.app.agent.download_image")
def test_audit_product_with_gemini_multimodal(mock_download_image, mock_get_gemini_client, mock_key_configured, mock_is_mock_mode):
    # Set up mocks
    def mock_download_side_effect(url):
        if "good" in url:
            return b"dummy_png_bytes", "image/png"
        else:
            raise Exception("Failed to download")
    mock_download_image.side_effect = mock_download_side_effect
    
    # Mock Gemini client and response
    mock_client = MagicMock()
    mock_get_gemini_client.return_value = mock_client
    
    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"suggested_title": "Otimizado", "suggested_description": "Otimizado", "missing_attributes": [], "image_issues": [], "seo_score": 80}'
    mock_gemini_response.usage_metadata = MagicMock()
    mock_gemini_response.usage_metadata.prompt_token_count = 10
    mock_gemini_response.usage_metadata.candidates_token_count = 20
    
    mock_client.models.generate_content.return_value = mock_gemini_response
    
    images = ["https://example.com/good1.png", "https://example.com/bad2.png"]
    
    result, tokens_in, tokens_out, latency = audit_product_with_gemini(
        title="Produto Teste",
        description="Descrição Teste",
        images=images,
        category="Cat",
        price=10.0,
        marketplace="mercado_livre"
    )
    
    # Check that download_image was called for all images
    assert mock_download_image.call_count == 2
    
    # Check that client.models.generate_content was called with a contents list containing one text part and one image Part
    mock_client.models.generate_content.assert_called_once()
    call_args = mock_client.models.generate_content.call_args[1]
    contents = call_args["contents"]
    
    assert len(contents) == 2  # 1 text block + 1 successfully downloaded image Part
    assert isinstance(contents[0], str)
    assert isinstance(contents[1], types.Part)
    
    # Verify that the failed download was graciosamente added to the image_issues
    assert len(result.image_issues) == 1
    assert result.image_issues[0].image_url == "https://example.com/bad2.png"
    assert result.image_issues[0].issue == "não foi possível acessar a imagem"
    assert result.image_issues[0].severity == "MEDIUM"
