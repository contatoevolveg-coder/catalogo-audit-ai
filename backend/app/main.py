import os
import json
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from backend.app.config import CORS_ORIGINS
from backend.app.constants import ProductStatus, SuggestionStatus
from backend.app.database import init_db, get_db, Product, Suggestion, AuditLog
from backend.app.agent import audit_product_with_gemini
from backend.app.schemas import (
    ProductCreate, ProductResponse, SuggestionResponse, AuditLogResponse
)

# Logger setup (configuração central de logging da aplicação)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o banco de dados e cria as tabelas
    logger.info("Inicializando o banco de dados...")
    init_db()
    yield

app = FastAPI(
    title="Catálogo Audit AI Agent - FastAPI Backend",
    description="Fase 0 - Agente de auditoria de anúncios para marketplaces brasileiros.",
    lifespan=lifespan
)

# Configuração de CORS restrita às origens definidas em config (env CORS_ORIGINS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# Serviço de auditoria (lógica reutilizável)
# -------------------------------------------------------------

def perform_product_audit(product: Product, db: Session) -> Suggestion:
    """Executa a auditoria de um produto via Gemini e persiste log e sugestão.

    Função de serviço reutilizada tanto pelo endpoint individual quanto pelo
    de lote — evita chamar handlers de rota diretamente. Comita a transação.
    """
    input_payload = {
        "title": product.title,
        "description": product.description,
        "images": product.images or [],
        "category": product.category,
        "price": product.price,
        "marketplace": product.marketplace,
    }

    audit_result, tokens_in, tokens_out, latency = audit_product_with_gemini(
        title=product.title,
        description=product.description,
        images=product.images or [],
        category=product.category or "",
        price=product.price or 0.0,
        marketplace=product.marketplace,
    )

    output_payload = audit_result.model_dump()

    # 1. Salva log de Auditoria
    db_log = AuditLog(
        product_id=product.id,
        input_payload=input_payload,
        output_payload=output_payload,
        model_used=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        token_cost_usd=0.0,  # Tier gratuito do Google AI Studio
        latency_seconds=latency,
    )
    db.add(db_log)

    # 2. Remove sugestão anterior (se houver) e cria a nova
    existing_suggestion = db.query(Suggestion).filter(Suggestion.product_id == product.id).first()
    if existing_suggestion:
        db.delete(existing_suggestion)

    db_suggestion = Suggestion(
        product_id=product.id,
        suggested_title=audit_result.suggested_title,
        suggested_description=audit_result.suggested_description,
        missing_attributes=output_payload.get("missing_attributes", []),
        image_issues=output_payload.get("image_issues", []),
        seo_score=audit_result.seo_score,
        status=SuggestionStatus.PENDING.value,
    )
    db.add(db_suggestion)

    # 3. Atualiza o status do produto
    product.status = ProductStatus.AUDITED.value

    db.commit()
    db.refresh(db_suggestion)
    return db_suggestion

# -------------------------------------------------------------
# Endpoints de Produtos
# -------------------------------------------------------------

@app.get("/products", response_model=List[ProductResponse])
def list_products(db: Session = Depends(get_db)):
    """Lista todos os produtos cadastrados."""
    return db.query(Product).order_by(Product.id.desc()).all()

@app.post("/products", response_model=ProductResponse)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    """Cria um novo produto manualmente."""
    db_product = Product(
        title=product.title,
        description=product.description,
        images=product.images or [],
        category=product.category,
        price=product.price,
        marketplace=product.marketplace.value,
        status=ProductStatus.PENDING.value,
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@app.post("/products/import-test-listings")
def import_test_listings(db: Session = Depends(get_db)):
    """Importa os anúncios do arquivo test_listings.json na raiz do projeto."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    test_file_path = os.path.join(project_root, "test_listings.json")

    if not os.path.exists(test_file_path):
        raise HTTPException(
            status_code=404,
            detail="Arquivo test_listings.json não encontrado na raiz do projeto."
        )

    try:
        with open(test_file_path, "r", encoding="utf-8") as f:
            listings = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Erro ao ler test_listings.json: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao ler test_listings.json: {str(e)}"
        )

    imported_count = 0
    for item in listings:
        # Evita duplicidade simples checando por título e marketplace
        exists = db.query(Product).filter(
            Product.title == item["title"],
            Product.marketplace == item["marketplace"]
        ).first()

        if not exists:
            db_product = Product(
                title=item["title"],
                description=item["description"],
                images=item.get("images", []),
                category=item.get("category", ""),
                price=item.get("price", 0.0),
                marketplace=item["marketplace"],
                status=ProductStatus.PENDING.value,
            )
            db.add(db_product)
            imported_count += 1

    db.commit()
    return {"message": f"Sucesso! {imported_count} novos anúncios de teste importados."}

# -------------------------------------------------------------
# Endpoints de Auditoria (Agente)
# -------------------------------------------------------------

@app.post("/products/{product_id}/audit", response_model=SuggestionResponse)
def audit_product(product_id: int, db: Session = Depends(get_db)):
    """
    Aciona o Agente de IA para auditar um produto específico.
    Invoca o Gemini com structured outputs, calcula tokens e salva sugestões e logs.
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    try:
        return perform_product_audit(product, db)
    except ValueError as ve:
        # Erro de falta de API Key do Gemini
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        db.rollback()
        logger.exception(f"Erro na auditoria do produto {product_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno ao processar auditoria com o Gemini: {str(e)}"
        )

@app.post("/products/audit-all")
def audit_all_pending(db: Session = Depends(get_db)):
    """Audita todos os produtos com status 'pending'."""
    pending_products = db.query(Product).filter(
        Product.status == ProductStatus.PENDING.value
    ).all()
    if not pending_products:
        return {"message": "Nenhum produto pendente para auditar."}

    success_count = 0
    errors = []

    for product in pending_products:
        try:
            perform_product_audit(product, db)
            success_count += 1
        except Exception as e:
            db.rollback()
            logger.exception(f"Erro ao auditar produto {product.id} no lote")
            errors.append({"product_id": product.id, "error": str(e)})

    return {
        "message": f"Auditoria concluída. {success_count} produtos auditados com sucesso.",
        "failed_count": len(errors),
        "errors": errors
    }

# -------------------------------------------------------------
# Endpoints de Sugestões (Aprovação/Rejeição)
# -------------------------------------------------------------

@app.get("/products/{product_id}/suggestions", response_model=List[SuggestionResponse])
def get_product_suggestions(product_id: int, db: Session = Depends(get_db)):
    """Obtém o histórico de sugestões de um produto."""
    return db.query(Suggestion).filter(Suggestion.product_id == product_id).all()

@app.post("/suggestions/{suggestion_id}/approve", response_model=SuggestionResponse)
def approve_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    """
    Aprova a sugestão gerada pela IA.
    Aplica as melhorias (título e descrição) no produto original e atualiza o status do produto.
    """
    suggestion = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada.")

    product = db.query(Product).filter(Product.id == suggestion.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produto original não encontrado.")

    # Aplica as mudanças
    product.title = suggestion.suggested_title
    product.description = suggestion.suggested_description
    product.status = ProductStatus.OPTIMIZED.value

    # Atualiza o status da sugestão
    suggestion.status = SuggestionStatus.APPROVED.value
    suggestion.reviewed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(suggestion)
    return suggestion

@app.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionResponse)
def reject_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    """Rejeita a sugestão gerada pela IA."""
    suggestion = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada.")

    # Apenas atualiza o status da sugestão e mantém o produto com o texto original
    suggestion.status = SuggestionStatus.REJECTED.value
    suggestion.reviewed_at = datetime.now(timezone.utc)

    # O produto permanece como 'audited': a auditoria foi concluída, mas as
    # sugestões foram negadas.
    db.commit()
    db.refresh(suggestion)
    return suggestion

# -------------------------------------------------------------
# Endpoints de Logs e Saúde
# -------------------------------------------------------------

@app.get("/health")
def health_check():
    """Health check simples para monitoramento."""
    return {"status": "ok"}

@app.get("/logs", response_model=List[AuditLogResponse])
def list_logs(db: Session = Depends(get_db)):
    """Lista todos os logs de auditoria para análise de tokens, custo e latência."""
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()
