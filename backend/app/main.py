import os
import json
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.app.config import CORS_ORIGINS
from backend.app.constants import ProductStatus, SuggestionStatus
from backend.app.database import init_db, get_db, Product, Suggestion, AuditLog, User, SchedulerRun, Alert
from backend.app.agent import audit_product_with_gemini
from backend.app.schemas import (
    ProductCreate, ProductResponse, SuggestionResponse, AuditLogResponse, SchedulerRunResponse, AlertResponse,
    ApproveSuggestionRequest, FeedbackStatsResponse, ProductUpdateRequest, ProductDeleteRequest
)
from backend.app.routers import imports, credentials, marketplace_publish, erp_bling, auth, customer_questions, oauth, orders, webhooks
from backend.app.services.audit_service import perform_product_audit
from backend.app.services.marketplace_manage_service import update_product as svc_update_product, delete_product as svc_delete_product
from backend.app.security.dependencies import get_current_user, get_current_tenant, require_admin



# Logger setup (configuração central de logging da aplicação)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o banco de dados e cria as tabelas. Em serverless não deixamos
    # uma falha aqui derrubar toda a aplicação (as tabelas já existem no Postgres).
    logger.info("Inicializando o banco de dados...")
    try:
        init_db()
    except Exception:
        logger.exception("Falha ao inicializar o banco de dados (seguindo mesmo assim).")
        
    try:
        from backend.app.scheduler import start_scheduler
        start_scheduler()
    except Exception:
        logger.exception("Falha ao inicializar o scheduler (seguindo mesmo assim).")
        
    yield
    
    try:
        from backend.app.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        logger.exception("Falha ao encerrar o scheduler.")


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

# Registra as rotas da Fase 1A (Cadastro em massa)
app.include_router(imports.router)
app.include_router(credentials.router)
app.include_router(marketplace_publish.router)
app.include_router(erp_bling.router)
app.include_router(auth.router)
app.include_router(customer_questions.router)
app.include_router(oauth.router)
app.include_router(orders.router)
app.include_router(webhooks.router)


@app.get("/", include_in_schema=False)
def root():
    """Redireciona a raiz da API para a documentação interativa (Swagger)."""
    return RedirectResponse(url="/docs")






# -------------------------------------------------------------
# Endpoints de Produtos
# -------------------------------------------------------------

@app.get("/products", response_model=List[ProductResponse])
def list_products(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Lista todos os produtos cadastrados do tenant."""
    return db.query(Product).filter(Product.tenant_id == tenant_id).order_by(Product.id.desc()).all()

@app.post("/products", response_model=ProductResponse)
def create_product(product: ProductCreate, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Cria um novo produto manualmente."""
    db_product = Product(
        tenant_id=tenant_id,
        title=product.title,
        description=product.description,
        images=product.images or [],
        category=product.category,
        price=product.price,
        marketplace=product.marketplace.value,
        status=ProductStatus.PENDING.value,
        available_quantity=product.available_quantity,
        condition=product.condition,
        attributes=product.attributes,
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@app.put("/products/{product_id}", response_model=ProductResponse)
def update_product_endpoint(
    product_id: int,
    data: ProductUpdateRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant)
):
    """Edita um anúncio (produto). Se sync_to_ml=True e o produto estiver publicado,
    replica a alteração no Mercado Livre. Disponível para admin e analista."""
    changes = data.model_dump(exclude={"sync_to_ml", "credential_id"}, exclude_none=True)
    return svc_update_product(
        product_id=product_id,
        tenant_id=tenant_id,
        db=db,
        changes=changes,
        credential_id=data.credential_id,
        sync_to_ml=data.sync_to_ml,
    )

@app.delete("/products/{product_id}")
def delete_product_endpoint(
    product_id: int,
    credential_id: Optional[int] = None,
    close_on_marketplace: bool = True,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin)
):
    """Exclui um anúncio (produto). Operação RESTRITA a usuários 'admin'.
    Se o produto estiver publicado no ML e houver credencial, encerra o anúncio lá também."""
    return svc_delete_product(
        product_id=product_id,
        tenant_id=admin_user.tenant_id,
        db=db,
        credential_id=credential_id,
        close_on_marketplace=close_on_marketplace,
    )

@app.post("/products/import-test-listings")
def import_test_listings(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
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
        # Evita duplicidade simples checando por título e marketplace e tenant
        exists = db.query(Product).filter(
            Product.title == item["title"],
            Product.marketplace == item["marketplace"],
            Product.tenant_id == tenant_id
        ).first()

        if not exists:
            db_product = Product(
                tenant_id=tenant_id,
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
def audit_product(product_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """
    Aciona o Agente de IA para auditar um produto específico.
    Invoca o Gemini com structured outputs, calcula tokens e salva sugestões e logs.
    """
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
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
def audit_all_pending(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Audita todos os produtos com status 'pending'."""
    pending_products = db.query(Product).filter(
        Product.status == ProductStatus.PENDING.value,
        Product.tenant_id == tenant_id
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
def get_product_suggestions(product_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Obtém o histórico de sugestões de um produto."""
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
    return db.query(Suggestion).filter(Suggestion.product_id == product_id).all()

@app.post("/suggestions/{suggestion_id}/approve", response_model=SuggestionResponse)
def approve_suggestion(
    suggestion_id: int, 
    request_data: ApproveSuggestionRequest = None,
    db: Session = Depends(get_db), 
    tenant_id: int = Depends(get_current_tenant)
):
    """
    Aprova a sugestão gerada pela IA, aceitando edições finais opcionais do usuário.
    Aplica as melhorias no produto original e atualiza o status do produto.
    Calcula e salva o diff (SuggestionFeedback) para few-shot tuning futuro.
    """
    from backend.app.database import SuggestionFeedback
    import difflib

    suggestion = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada.")

    product = db.query(Product).filter(Product.id == suggestion.product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produto original não encontrado ou não pertence a você.")

    # Valores finais (sugeridos pela IA por padrão, sobrescritos se editado)
    final_title = suggestion.suggested_title
    final_description = suggestion.suggested_description

    if request_data:
        if request_data.final_title:
            final_title = request_data.final_title
        if request_data.final_description:
            final_description = request_data.final_description

    # Aplica as mudanças no produto
    product.title = final_title
    product.description = final_description
    product.status = ProductStatus.OPTIMIZED.value

    # Atualiza o status da sugestão
    suggestion.status = SuggestionStatus.APPROVED.value
    suggestion.reviewed_at = datetime.now(timezone.utc)

    # Gravar o feedback (Title)
    title_ratio = difflib.SequenceMatcher(None, suggestion.suggested_title, final_title).ratio()
    title_edited = title_ratio < 1.0
    fb_title = SuggestionFeedback(
        suggestion_id=suggestion.id,
        field="title",
        ai_value=suggestion.suggested_title,
        human_value=final_title,
        was_edited=title_edited,
        edit_distance=1.0 - title_ratio
    )
    db.add(fb_title)

    # Gravar o feedback (Description)
    desc_ratio = difflib.SequenceMatcher(None, suggestion.suggested_description, final_description).ratio()
    desc_edited = desc_ratio < 1.0
    fb_desc = SuggestionFeedback(
        suggestion_id=suggestion.id,
        field="description",
        ai_value=suggestion.suggested_description,
        human_value=final_description,
        was_edited=desc_edited,
        edit_distance=1.0 - desc_ratio
    )
    db.add(fb_desc)

    db.commit()
    db.refresh(suggestion)
    return suggestion

@app.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionResponse)
def reject_suggestion(suggestion_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Rejeita a sugestão gerada pela IA."""
    suggestion = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada.")

    product = db.query(Product).filter(Product.id == suggestion.product_id, Product.tenant_id == tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Acesso negado.")

    # Apenas atualiza o status da sugestão e mantém o produto com o texto original
    suggestion.status = SuggestionStatus.REJECTED.value
    suggestion.reviewed_at = datetime.now(timezone.utc)

    # O produto permanece como 'audited': a auditoria foi concluída, mas as
    # sugestões foram negadas.
    db.commit()
    db.refresh(suggestion)
    return suggestion

# -------------------------------------------------------------
# Endpoints de Feedback
# -------------------------------------------------------------

@app.get("/feedback/stats", response_model=FeedbackStatsResponse)
def get_feedback_stats(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    from backend.app.database import SuggestionFeedback
    
    feedbacks = db.query(SuggestionFeedback).join(Suggestion).join(Product).filter(Product.tenant_id == tenant_id).all()
    if not feedbacks:
        return {
            "total_approved": 0,
            "total_edited": 0,
            "edit_percentage": 0.0,
            "avg_edit_distance": 0.0,
            "most_edited_field": None
        }

    total_feedbacks = len(feedbacks)
    # Divide por 2 pois cada sugestão aprovada gera 2 feedbacks (título e descrição)
    total_approved = total_feedbacks // 2 
    
    # Consideramos uma sugestão editada se pelo menos um dos campos foi editado
    edited_suggestion_ids = set([fb.suggestion_id for fb in feedbacks if fb.was_edited])
    total_edited = len(edited_suggestion_ids)
    edit_percentage = (total_edited / total_approved) * 100 if total_approved > 0 else 0.0

    avg_edit_distance = sum([fb.edit_distance for fb in feedbacks]) / total_feedbacks
    
    title_edits = len([fb for fb in feedbacks if fb.field == "title" and fb.was_edited])
    desc_edits = len([fb for fb in feedbacks if fb.field == "description" and fb.was_edited])
    most_edited = "title" if title_edits > desc_edits else ("description" if desc_edits > title_edits else "both")

    return {
        "total_approved": total_approved,
        "total_edited": total_edited,
        "edit_percentage": edit_percentage,
        "avg_edit_distance": avg_edit_distance,
        "most_edited_field": most_edited
    }

# -------------------------------------------------------------
# Endpoints de Logs e Saúde
# -------------------------------------------------------------

@app.get("/health")
def health_check():
    """Health check simples para monitoramento."""
    return {"status": "ok"}

@app.get("/logs", response_model=List[AuditLogResponse])
def list_logs(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Lista todos os logs de auditoria para análise de tokens, custo e latência."""
    return db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id).order_by(AuditLog.created_at.desc()).all()

@app.get("/scheduler/status", response_model=List[SchedulerRunResponse])
def get_scheduler_status(db: Session = Depends(get_db), admin_user: User = Depends(require_admin)):
    """Retorna as últimas execuções de jobs do scheduler agendado.

    Restrito a admins: os registros do scheduler são globais (sem escopo de
    tenant) e o campo 'errors' pode conter detalhes de execução de vários tenants.
    """
    return db.query(SchedulerRun).order_by(SchedulerRun.start_time.desc()).limit(100).all()

# -------------------------------------------------------------
# Endpoints de Alertas do Sistema
# -------------------------------------------------------------

@app.get("/alerts", response_model=List[AlertResponse])
def list_alerts(is_read: Optional[bool] = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Lista os alertas do sistema com filtro opcional por status de leitura."""
    query = db.query(Alert).filter(Alert.tenant_id == tenant_id)
    if is_read is not None:
        query = query.filter(Alert.is_read == is_read)
    return query.order_by(Alert.created_at.desc()).all()

@app.post("/alerts/{alert_id}/read", response_model=AlertResponse)
def mark_alert_as_read(alert_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Marca um alerta específico como lido."""
    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.tenant_id == tenant_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerta não encontrado.")
    alert.is_read = True
    db.commit()
    db.refresh(alert)
    return alert

@app.post("/alerts/trigger")
def trigger_alert_generation(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant)):
    """Executa manualmente a verificação e geração de alertas para a organização do usuário."""
    from backend.app.services.alert_service import check_and_generate_alerts
    created = check_and_generate_alerts(db, tenant_id)
    return {"message": f"Verificação de alertas concluída. {created} alertas recém-criados.", "created": created}
