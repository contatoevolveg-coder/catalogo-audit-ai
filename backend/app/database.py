import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from backend.app.config import DATABASE_URL as CONFIG_DATABASE_URL


def _utcnow() -> datetime:
    """Timestamp atual com timezone UTC (substitui o depreciado datetime.utcnow)."""
    return datetime.now(timezone.utc)


# Usa a DATABASE_URL central; se vazia, faz fallback para SQLite local.
DATABASE_URL = CONFIG_DATABASE_URL

if not DATABASE_URL or DATABASE_URL.strip() == "":
    # Localiza o arquivo de banco na raiz do projeto
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.abspath(os.path.join(project_root, "catalog_audit.db"))
    DATABASE_URL = f"sqlite:///{db_path}"
    engine_args = {"connect_args": {"check_same_thread": False}}
else:
    engine_args = {}

engine = create_engine(DATABASE_URL, **engine_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    description = Column(String, nullable=True)
    images = Column(JSON, nullable=True)  # Lista de URLs das imagens
    category = Column(String, nullable=True)
    price = Column(Float, nullable=True)
    marketplace = Column(String, nullable=False, index=True)  # mercado_livre, shopee, amazon, magalu, etc.
    status = Column(String, default="pending", index=True)    # pending, audited, optimized
    created_at = Column(DateTime, default=_utcnow)

    # Relacionamentos
    suggestions = relationship("Suggestion", back_populates="product", cascade="all, delete-orphan")
    logs = relationship("AuditLog", back_populates="product", cascade="all, delete-orphan")

class Suggestion(Base):
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True, index=True)
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

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
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

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
