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
    available_quantity: Optional[int] = Field(default=None, ge=0, description="Estoque disponível")
    condition: Optional[str] = Field(default=None, description="Condição: new ou used")
    attributes: Optional[Dict[str, Any]] = Field(default=None, description="Atributos extras (marca, modelo, gtin_ean)")

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

# -------------------------------------------------------------
# Novos Esquemas para a Fase 1A (Cadastro em Massa)
# -------------------------------------------------------------
class ValidationError(BaseModel):
    field: str = Field(description="Campo que gerou o erro/aviso")
    code: str = Field(description="Código curto identificando a falha")
    message: str = Field(description="Mensagem explicativa para o usuário")
    severity: str = Field(description="Severidade: 'error' (bloqueia) ou 'warning' (aviso)")

class UploadResponse(BaseModel):
    batch_id: int = Field(description="ID do lote (batch) de importação criado")
    detected_columns: List[str] = Field(description="Lista de cabeçalhos identificados na planilha")
    sample_rows: List[Dict[str, Any]] = Field(description="Amostra das primeiras linhas brutas da planilha")
    suggested_mapping: Dict[str, str] = Field(description="Mapeamento sugerido automaticamente de de-para")

class ColumnMappingRequest(BaseModel):
    mapping: Dict[str, str] = Field(description="Mapeamento completo (coluna_planilha -> campo_canonico)")

class ImportRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    row_number: int
    raw_data: Dict[str, Any]
    mapped_data: Optional[Dict[str, Any]] = None
    validation_status: str
    validation_errors: Optional[List[ValidationError]] = None
    product_id: Optional[int] = None

class ValidationSummary(BaseModel):
    total: int = Field(description="Total de linhas processadas")
    valid: int = Field(description="Total de linhas válidas")
    invalid: int = Field(description="Total de linhas inválidas com erros de bloqueio")
    with_warnings: int = Field(description="Total de linhas contendo avisos")

class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    marketplace: str
    status: str
    column_mapping: Optional[Dict[str, str]] = None
    total_rows: int
    valid_rows: int
    invalid_rows: int
    created_at: datetime

class ConfirmImportResponse(BaseModel):
    imported: int = Field(description="Número de produtos cadastrados com sucesso")
    skipped_invalid: int = Field(description="Número de linhas puladas por conterem erros")
    created_product_ids: List[int] = Field(description="Lista com os IDs dos produtos criados")
