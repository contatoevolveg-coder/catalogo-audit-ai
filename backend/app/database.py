import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, JSON, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from backend.app.config import DATABASE_URL as CONFIG_DATABASE_URL


def _utcnow() -> datetime:
    """Timestamp atual com timezone UTC (substitui o depreciado datetime.utcnow)."""
    return datetime.now(timezone.utc)


# Usa a DATABASE_URL central; se vazia, faz fallback para SQLite local.
DATABASE_URL = CONFIG_DATABASE_URL

if not DATABASE_URL or DATABASE_URL.strip() == "":
    # Fallback para SQLite. Em ambiente serverless (Vercel) o diretório do
    # projeto é somente-leitura, então usamos /tmp (gravável, porém efêmero).
    if os.getenv("VERCEL"):
        db_path = "/tmp/catalog_audit.db"
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        db_path = os.path.abspath(os.path.join(project_root, "catalog_audit.db"))
    DATABASE_URL = f"sqlite:///{db_path}"
    engine_args = {"connect_args": {"check_same_thread": False}}
else:
    engine_args = {}

engine = create_engine(DATABASE_URL, **engine_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    plan = Column(String, default="free")
    created_at = Column(DateTime, default=_utcnow)

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    title = Column(String, nullable=False, index=True)
    description = Column(String, nullable=True)
    images = Column(JSON, nullable=True)  # Lista de URLs das imagens
    category = Column(String, nullable=True)
    price = Column(Float, nullable=True)
    marketplace = Column(String, nullable=False, index=True)  # mercado_livre, shopee, amazon, magalu, etc.
    status = Column(String, default="pending", index=True)    # pending, audited, optimized
    available_quantity = Column(Integer, nullable=True)       # estoque disponível
    condition = Column(String, nullable=True)                 # new, used
    attributes = Column(JSON, nullable=True)                  # {brand, model, gtin_ean, ...}
    external_listing_id = Column(String, nullable=True)       # ID retornado após publicação com sucesso
    erp_sku = Column(String, nullable=True, index=True)       # SKU do Bling ERP vinculado manualmente
    created_at = Column(DateTime, default=_utcnow)



    # Relacionamentos
    suggestions = relationship("Suggestion", back_populates="product", cascade="all, delete-orphan")
    logs = relationship("AuditLog", back_populates="product", cascade="all, delete-orphan")

class Suggestion(Base):
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    suggested_title = Column(String, nullable=False)
    suggested_description = Column(String, nullable=False)
    missing_attributes = Column(JSON, nullable=True)  # List[dict]: {"name": "...", "recommended_value": "...", "reason": "..."}
    image_issues = Column(JSON, nullable=True)        # List[dict]: {"image_url": "...", "issue": "...", "severity": "..."}
    seo_score = Column(Integer, nullable=False)       # 0 a 100
    status = Column(String, default="pending", index=True)  # pending, approved, rejected
    created_at = Column(DateTime, default=_utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    product = relationship("Product", back_populates="suggestions")

class SuggestionFeedback(Base):
    __tablename__ = "suggestion_feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    suggestion_id = Column(Integer, ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False, index=True)
    field = Column(String, nullable=False)  # "title" ou "description"
    ai_value = Column(String, nullable=False)
    human_value = Column(String, nullable=False)
    was_edited = Column(Boolean, default=False, nullable=False)
    edit_distance = Column(Float, nullable=False)  # 0.0 a 1.0
    created_at = Column(DateTime, default=_utcnow)

    # Dados serão usados futuramente para few-shot tuning por usuário/marketplace.
    suggestion = relationship("Suggestion")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    input_payload = Column(JSON, nullable=False)
    output_payload = Column(JSON, nullable=True)
    model_used = Column(String, nullable=False)
    tokens_input = Column(Integer, nullable=False)
    tokens_output = Column(Integer, nullable=False)
    token_cost_usd = Column(Float, default=0.0)
    latency_seconds = Column(Float, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    product = relationship("Product", back_populates="logs")

class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    marketplace = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="uploaded", index=True)
    column_mapping = Column(JSON, nullable=True)
    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    invalid_rows = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)

    # Relacionamentos
    rows = relationship("ImportRow", back_populates="batch", cascade="all, delete-orphan")

class ImportRow(Base):
    __tablename__ = "import_rows"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    raw_data = Column(JSON, nullable=False)
    mapped_data = Column(JSON, nullable=True)
    validation_status = Column(String, default="pending", index=True)
    validation_errors = Column(JSON, nullable=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    # Relacionamentos
    batch = relationship("ImportBatch", back_populates="rows")
    product = relationship("Product")

class ExternalCallLog(Base):
    __tablename__ = "external_call_logs"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False, index=True)  # ex: image_check
    target_url = Column(String, nullable=False)
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, default=False)
    latency_seconds = Column(Float, nullable=False)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

class Credential(Base):
    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)  # ex.: "mercado_livre", "shopee", "bling"
    provider_type = Column(String, nullable=False)  # "marketplace" ou "erp"
    label = Column(String, nullable=False)  # nome amigável dado pelo usuário
    encrypted_secret = Column(Text, nullable=False)  # payload do Fernet (string base64)
    masked_preview = Column(String, nullable=False)  # calculado na criação/rotação, ex. "••••7f3a"
    scopes = Column(JSON, nullable=False)  # lista de strings validadas contra um conjunto permitido
    status = Column(String, default="untested", nullable=False, index=True)  # untested|valid|expired|error
    status_detail = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)  # Adicionado para Fase 10 (OAuth2)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

class MarketplacePublication(Base):
    __tablename__ = "marketplace_publications"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id = Column(Integer, ForeignKey("credentials.id"), nullable=False, index=True)
    marketplace_item_id = Column(String, nullable=True)  # ID retornado pelo ML (ex.: "MLB123456")
    category_id = Column(String, nullable=False)  # categoria do ML usada na publicação
    status = Column(String, nullable=False, index=True)  # "success" ou "error"
    error_detail = Column(Text, nullable=True)  # mensagem/corpo de erro do ML quando falha
    request_payload = Column(JSON, nullable=False)  # payload enviado ao ML (SEM o token)
    response_payload = Column(JSON, nullable=True)  # resposta crua do ML (sucesso ou erro)
    created_at = Column(DateTime, default=_utcnow)

    # Relacionamentos
    product = relationship("Product")
    credential = relationship("Credential")

class ErpSyncLog(Base):
    __tablename__ = "erp_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id = Column(Integer, ForeignKey("credentials.id"), nullable=False, index=True)
    erp_sku = Column(String, nullable=False)
    sync_type = Column(String, default="stock_pull", nullable=False)
    status = Column(String, nullable=False, index=True)  # success | error | not_found
    previous_quantity = Column(Integer, nullable=True)
    new_quantity = Column(Integer, nullable=True)
    error_detail = Column(Text, nullable=True)
    response_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    # Relacionamentos
    product = relationship("Product")
    credential = relationship("Credential")




class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="admin")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String, index=True, nullable=False)  # "login" ou "register"
    identifier = Column(String, index=True, nullable=False)  # username ou IP
    ip_address = Column(String, index=True, nullable=True)
    success = Column(Boolean, nullable=False)
    created_at = Column(DateTime, default=_utcnow, index=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    state = Column(String, unique=True, index=True, nullable=False)
    provider = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class CustomerQuestion(Base):
    __tablename__ = "customer_questions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    credential_id = Column(Integer, ForeignKey("credentials.id"), nullable=False, index=True)
    ml_question_id = Column(String, unique=True, index=True, nullable=False)
    item_id = Column(String, index=True, nullable=False)
    matched_product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    question_text = Column(Text, nullable=False)
    asker_nickname = Column(String, nullable=True)
    status = Column(String, default="pending_draft", index=True, nullable=False)  # pending_draft | draft_ready | approved_sent | error | dismissed
    ai_suggested_answer = Column(Text, nullable=True)
    final_answer_text = Column(Text, nullable=True)
    needs_human_review = Column(Boolean, default=False, nullable=False)
    review_reason = Column(Text, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    latency_seconds = Column(Float, nullable=True)
    ml_created_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=_utcnow, index=True, nullable=False)
    answered_at = Column(DateTime, nullable=True)

    # Relacionamentos
    credential = relationship("Credential")
    product = relationship("Product")


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id = Column(Integer, primary_key=True, index=True)
    job = Column(String, nullable=False, index=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    items_processed = Column(Integer, default=0, nullable=False)
    errors = Column(Text, nullable=True)

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    marketplace = Column(String, nullable=False, index=True)
    external_order_id = Column(String, unique=True, nullable=False, index=True)
    credential_id = Column(Integer, ForeignKey("credentials.id"), nullable=False, index=True)
    buyer_nickname = Column(String, nullable=True)
    total_amount = Column(Float, nullable=False)
    status = Column(String, nullable=False, index=True)
    shipping_status = Column(String, nullable=True)
    stock_deducted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    # Relacionamentos
    credential = relationship("Credential")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    sku = Column(String, nullable=True)
    title = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)

    # Relacionamentos
    order = relationship("Order", back_populates="items")
    product = relationship("Product")

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    type = Column(String, nullable=False, index=True)  # low_stock | credential_expiring | low_seo_score | answer_pending
    severity = Column(String, nullable=False)  # HIGH | MEDIUM | LOW
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)
    credential_id = Column(Integer, ForeignKey("credentials.id", ondelete="CASCADE"), nullable=True, index=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    # Relacionamentos
    product = relationship("Product")
    credential = relationship("Credential")


def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
