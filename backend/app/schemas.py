from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import List, Optional, Dict, Any, Literal
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
    external_listing_id: Optional[str] = None
    marketplace_status: Optional[str] = None
    erp_sku: Optional[str] = None
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

class ApproveSuggestionRequest(BaseModel):
    final_title: Optional[str] = Field(default=None, description="Título final editado pelo humano (opcional)")
    final_description: Optional[str] = Field(default=None, description="Descrição final editada pelo humano (opcional)")

class FeedbackStatsResponse(BaseModel):
    total_approved: int
    total_edited: int
    edit_percentage: float
    avg_edit_distance: float
    most_edited_field: Optional[str] = None

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
    audited: int = 0
    audit_skipped: int = 0
    audit_errors: List[Dict[str, Any]] = []

# -------------------------------------------------------------
# Esquemas para a Fase 3 (Cofre de Credenciais)
# -------------------------------------------------------------
from backend.app.constants import ALLOWED_CREDENTIAL_SCOPES

class CredentialCreate(BaseModel):
    provider: str = Field(description="Nome do provedor, ex.: 'mercado_livre', 'shopee', 'bling'")
    provider_type: Literal["marketplace", "erp"] = Field(description="Tipo do provedor: marketplace ou erp")
    label: str = Field(min_length=1, description="Nome amigável da credencial")
    secret_payload: Dict[str, Any] = Field(description="Payload do segredo bruto (dicionário de chaves/tokens)")
    scopes: List[str] = Field(description="Escopos associados à credencial")

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: List[str]) -> List[str]:
        for scope in v:
            if scope not in ALLOWED_CREDENTIAL_SCOPES:
                raise ValueError(f"Escopo '{scope}' não é permitido. Escopos permitidos: {ALLOWED_CREDENTIAL_SCOPES}")
        return v

class CredentialUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, description="Nome amigável da credencial")
    scopes: Optional[List[str]] = Field(None, description="Escopos associados à credencial")
    secret_payload: Optional[Dict[str, Any]] = Field(None, description="Payload do segredo bruto (opcional para rotação)")

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for scope in v:
                if scope not in ALLOWED_CREDENTIAL_SCOPES:
                    raise ValueError(f"Escopo '{scope}' não é permitido. Escopos permitidos: {ALLOWED_CREDENTIAL_SCOPES}")
        return v

class CredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    provider_type: str
    label: str
    masked_preview: str
    scopes: List[str]
    status: str
    status_detail: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

class CredentialTestResponse(BaseModel):
    status: str
    status_detail: Optional[str] = None
    last_checked_at: Optional[datetime] = None


# -------------------------------------------------------------
# Esquemas para a Fase 4 (Publicação de Anúncios no Mercado Livre)
# -------------------------------------------------------------

class CategorySuggestion(BaseModel):
    category_id: str = Field(description="ID da categoria sugerida (ex.: 'MLB1234')")
    category_name: str = Field(description="Nome amigável da categoria (ex.: 'Calçados')")

class PublishRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do cofre (Fase 3) para publicação")
    category_id: str = Field(min_length=1, description="ID da categoria no Mercado Livre a publicar")

class ImportMlItemsRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do Mercado Livre no cofre")
    max_items: int = Field(100, ge=1, le=500, description="Limite máximo de anúncios a importar/atualizar")

class ImportMlItemsResponse(BaseModel):
    found: int = Field(description="Quantidade de anúncios encontrados na conta do vendedor")
    imported: int = Field(description="Quantidade de novos produtos criados no catálogo local")
    updated: int = Field(description="Quantidade de produtos já vinculados que foram atualizados")
    errors: List[Dict[str, Any]] = Field(description="Erros estruturados ocorridos durante a importação")

class MarketplacePublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    credential_id: int
    marketplace_item_id: Optional[str] = None
    category_id: str
    status: str
    error_detail: Optional[str] = None
    created_at: datetime


# -------------------------------------------------------------
# Esquemas para a Fase 5 (Sincronização de Estoque com o Bling)
# -------------------------------------------------------------

class ErpLinkRequest(BaseModel):
    erp_sku: str = Field(min_length=1, description="Código SKU do produto cadastrado no Bling ERP")

class ErpSyncLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    credential_id: int
    erp_sku: str
    sync_type: str
    status: str
    previous_quantity: Optional[int] = None
    new_quantity: Optional[int] = None
    error_detail: Optional[str] = None
    created_at: datetime

class BulkSyncResponse(BaseModel):
    synced: int = Field(description="Quantidade de produtos sincronizados com sucesso")
    not_found: int = Field(description="Quantidade de produtos não encontrados no Bling")
    errors: List[Dict[str, Any]] = Field(description="Erros estruturados que ocorreram no lote")

class SyncStockRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do Bling no cofre")

class BulkSyncRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do Bling no cofre")
    max_sync: int = Field(20, ge=1, le=100, description="Limite máximo de produtos a sincronizar no lote")

class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150, description="Nome de usuário")
    password: str = Field(min_length=6, max_length=128, description="Senha de acesso")

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool = True
    created_at: datetime

class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150, description="Nome de usuário do novo operador/cliente")
    password: str = Field(min_length=6, max_length=128, description="Senha de acesso")
    role: Literal["admin", "analista"] = Field(default="analista", description="Papel: 'admin' (pode excluir) ou 'analista' (não pode excluir)")

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# -------------------------------------------------------------
# Esquemas para Gestão de Anúncios (CRUD de produtos)
# -------------------------------------------------------------

class ProductUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=10000)
    price: Optional[float] = Field(default=None, ge=0)
    available_quantity: Optional[int] = Field(default=None, ge=0)
    category: Optional[str] = Field(default=None, max_length=300)
    condition: Optional[str] = Field(default=None)
    images: Optional[List[str]] = Field(default=None)
    attributes: Optional[Dict[str, Any]] = Field(default=None)
    sync_to_ml: bool = Field(default=False, description="Se True, replica a edição no anúncio do Mercado Livre")
    credential_id: Optional[int] = Field(default=None, description="Credencial ML usada quando sync_to_ml=True")

class ProductDeleteRequest(BaseModel):
    credential_id: Optional[int] = Field(default=None, description="Credencial ML para encerrar o anúncio no marketplace")
    close_on_marketplace: bool = Field(default=True, description="Se True, encerra/exclui o anúncio no Mercado Livre também")


# -------------------------------------------------------------
# Esquemas para a Fase 9 (Gestão de Perguntas Pré-venda)
# -------------------------------------------------------------

class GeminiQuestionDraftResponse(BaseModel):
    suggested_answer: str = Field(description="A sugestão de resposta para a pergunta pré-venda do Mercado Livre.")
    needs_human_review: bool = Field(default=False, description="Indica se a pergunta exige revisão humana obrigatória.")
    review_reason: Optional[str] = Field(default=None, description="Motivo pelo qual a revisão humana foi necessária.")

class SyncQuestionsRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do Mercado Livre no cofre")
    max_fetch: int = Field(20, ge=1, le=100, description="Quantidade máxima de perguntas a buscar")

class SendAnswerRequest(BaseModel):
    credential_id: int = Field(description="ID da credencial do Mercado Livre no cofre")
    final_text: Optional[str] = Field(default=None, description="Texto da resposta final. Se omitido, usa a sugestão da IA")

class CustomerQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    credential_id: int
    ml_question_id: str
    item_id: str
    matched_product_id: Optional[int] = None
    question_text: str
    asker_nickname: Optional[str] = None
    status: str
    ai_suggested_answer: Optional[str] = None
    final_answer_text: Optional[str] = None
    needs_human_review: bool
    review_reason: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    latency_seconds: Optional[float] = None
    ml_created_at: Optional[datetime] = None
    fetched_at: datetime
    answered_at: Optional[datetime] = None


# -------------------------------------------------------------
# Esquemas para a Fase 10 (OAuth2)
# -------------------------------------------------------------

class AuthorizationUrlResponse(BaseModel):
    authorization_url: str = Field(description="URL para redirecionar o usuário para conceder acesso")

class OAuthCallbackRequest(BaseModel):
    code: str = Field(description="Authorization code retornado pelo provedor")
    state: str = Field(description="State token gerado na inicialização")
    label: str = Field(description="Nome amigável para identificar a credencial criada")
    shop_id: Optional[int] = Field(default=None, description="ID da loja (obrigatório para Shopee)")


# -------------------------------------------------------------
# Esquemas para o Scheduler Status
# -------------------------------------------------------------

class SchedulerRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    start_time: datetime
    end_time: Optional[datetime] = None
    items_processed: int
    errors: Optional[str] = None

# -------------------------------------------------------------
# Esquemas para Alertas do Sistema
# -------------------------------------------------------------

class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    severity: str
    product_id: Optional[int] = None
    credential_id: Optional[int] = None
    message: str
    is_read: bool
    created_at: datetime


# -------------------------------------------------------------
# Esquemas para a Gestão de Vendas (Orders)
# -------------------------------------------------------------

class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    product_id: Optional[int] = None
    sku: Optional[str] = None
    title: str
    quantity: int
    unit_price: float

class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    marketplace: str
    external_order_id: str
    credential_id: int
    buyer_nickname: Optional[str] = None
    total_amount: float
    status: str
    shipping_status: Optional[str] = None
    stock_deducted: bool
    created_at: datetime
    items: List[OrderItemResponse] = []

class OrderSyncResponse(BaseModel):
    synced: int = Field(description="Quantidade de pedidos sincronizados")
    stock_deductions: int = Field(description="Quantidade de abatimentos de estoque locais realizados")
