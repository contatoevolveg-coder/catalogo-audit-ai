from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.app.constants import Marketplace, Severity

# -------------------------------------------------------------
# Esquema Pydantic para Structured Output do Gemini
# -------------------------------------------------------------
class MissingAttribute(BaseModel):
    name: str = Field(description="Nome do atributo faltante (ex: Marca, Voltagem, Cor, Material)")
    recommended_value: str = Field(description="Valor recomendado para preencher esse atributo com base no contexto")
    reason: str = Field(description="Explicação do porquê esse atributo é crítico para SEO ou para a política do marketplace")

class ImageIssue(BaseModel):
    image_url: str = Field(description="URL da imagem que contém o problema")
    issue: str = Field(description="Descrição detalhada do problema (ex: Fundo com detalhes/não branco, texto promocional na foto)")
    severity: Severity = Field(description="Severidade do problema encontrado: HIGH, MEDIUM ou LOW")

class GeminiAuditResponse(BaseModel):
    suggested_title: str = Field(description="Título otimizado para o marketplace de destino seguindo as restrições de caracteres e boas práticas")
    suggested_description: str = Field(description="Descrição otimizada e estruturada ideal para o marketplace de destino")
    missing_attributes: List[MissingAttribute] = Field(default=[], description="Lista de atributos técnicos recomendados que estão ausentes")
    image_issues: List[ImageIssue] = Field(default=[], description="Erros ou oportunidades de melhoria encontrados nas imagens enviadas")
    seo_score: int = Field(ge=0, le=100, description="Nota de qualidade global do anúncio original frente às políticas da plataforma (de 0 a 100)")

# -------------------------------------------------------------
# Esquemas Pydantic Gerais para a API FastAPI
# -------------------------------------------------------------
class ProductBase(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Título do anúncio")
    description: Optional[str] = Field(default=None, max_length=10000)
    images: Optional[List[str]] = None
    category: Optional[str] = Field(default=None, max_length=300)
    price: Optional[float] = Field(default=None, ge=0, description="Preço em reais; não pode ser negativo")
    marketplace: Marketplace

class ProductCreate(ProductBase):
    pass

class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime

class SuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    suggested_title: str
    suggested_description: str
    missing_attributes: Optional[List[Dict[str, Any]]] = None
    image_issues: Optional[List[Dict[str, Any]]] = None
    seo_score: int
    status: str
    created_at: datetime
    reviewed_at: Optional[datetime] = None

class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    input_payload: Dict[str, Any]
    output_payload: Optional[Dict[str, Any]] = None
    model_used: str
    tokens_input: int
    tokens_output: int
    token_cost_usd: float
    latency_seconds: float
    created_at: datetime
