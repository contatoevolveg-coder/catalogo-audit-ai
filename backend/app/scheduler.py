import os
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

from backend.app.database import SessionLocal, Credential, SchedulerRun, CustomerQuestion, Tenant
from backend.app.services.erp_sync_service import sync_all_linked_products
from backend.app.services.question_service import sync_pending_questions, generate_draft_answer
from backend.app.services.oauth_service import refresh_if_needed
from backend.app.config import (
    SCHEDULER_SYNC_STOCK_MINUTES,
    SCHEDULER_SYNC_QUESTIONS_MINUTES,
    SCHEDULER_REFRESH_TOKENS_MINUTES,
    SCHEDULER_CHECK_ALERTS_MINUTES
)
from backend.app.services.stock_reconciliation_service import reconcile_stock


logger = logging.getLogger("scheduler")
scheduler = None

def sync_bling_stock_job():
    logger.info("Iniciando job sync_bling_stock_job...")
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    items_processed = 0
    errors_list = []
    
    try:
        creds = db.query(Credential).filter(
            Credential.provider == "bling",
            Credential.status == "valid"
        ).all()
        
        for cred in creds:
            try:
                logs = sync_all_linked_products(cred.id, db, cred.tenant_id)
                items_processed += len(logs)
                for log in logs:
                    if log.status == "error":
                        errors_list.append(f"Credencial Bling {cred.id}, SKU {log.erp_sku}: {log.error_detail}")
            except Exception as e:
                logger.error(f"Erro ao sincronizar estoque para credencial Bling {cred.id}: {e}")
                errors_list.append(f"Credencial Bling {cred.id}: {str(e)}")
                
        # Grava o log de execução do job
        run_record = SchedulerRun(
            job="sync_bling_stock",
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            items_processed=items_processed,
            errors="\n".join(errors_list) if errors_list else None
        )
        db.add(run_record)
        db.commit()
    except Exception as e:
        logger.error(f"Erro geral no job sync_bling_stock_job: {e}")
        try:
            run_record = SchedulerRun(
                job="sync_bling_stock",
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                items_processed=items_processed,
                errors=str(e)
            )
            db.add(run_record)
            db.commit()
        except Exception as db_err:
            logger.error(f"Erro ao persistir erro de scheduler no banco: {db_err}")
    finally:
        db.close()


def sync_ml_questions_job():
    logger.info("Iniciando job sync_ml_questions_job...")
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    items_processed = 0
    errors_list = []
    
    try:
        creds = db.query(Credential).filter(
            Credential.provider == "mercado_livre",
            Credential.status == "valid"
        ).all()
        
        for cred in creds:
            try:
                res = sync_pending_questions(cred.id, db, cred.tenant_id)
                synced = res.get("synced", 0)
                
                # Gera rascunho para as perguntas pendentes sincronizadas
                pending_qs = db.query(CustomerQuestion).filter(
                    CustomerQuestion.credential_id == cred.id,
                    CustomerQuestion.status == "pending_draft"
                ).all()
                
                for q in pending_qs:
                    try:
                        generate_draft_answer(q.id, db, cred.tenant_id)
                        items_processed += 1
                    except Exception as e:
                        logger.error(f"Erro ao gerar rascunho para pergunta {q.id}: {e}")
                        errors_list.append(f"Pergunta ML {q.id}: {str(e)}")
            except Exception as e:
                logger.error(f"Erro ao sincronizar perguntas para credencial ML {cred.id}: {e}")
                errors_list.append(f"Credencial ML {cred.id}: {str(e)}")
                
        run_record = SchedulerRun(
            job="sync_ml_questions",
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            items_processed=items_processed,
            errors="\n".join(errors_list) if errors_list else None
        )
        db.add(run_record)
        db.commit()
    except Exception as e:
        logger.error(f"Erro geral no job sync_ml_questions_job: {e}")
        try:
            run_record = SchedulerRun(
                job="sync_ml_questions",
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                items_processed=items_processed,
                errors=str(e)
            )
            db.add(run_record)
            db.commit()
        except Exception as db_err:
            logger.error(f"Erro ao persistir erro de scheduler no banco: {db_err}")
    finally:
        db.close()


def refresh_tokens_job():
    logger.info("Iniciando job refresh_tokens_job...")
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    items_processed = 0
    errors_list = []
    
    try:
        creds = db.query(Credential).all()
        for cred in creds:
            try:
                if cred.provider in ["mercado_livre", "bling"]:
                    # Chama refresh_if_needed proativamente
                    refresh_if_needed(cred, db)
                    items_processed += 1
            except Exception as e:
                logger.error(f"Erro ao atualizar token para credencial {cred.id}: {e}")
                errors_list.append(f"Credencial {cred.id}: {str(e)}")
                
        run_record = SchedulerRun(
            job="refresh_tokens",
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            items_processed=items_processed,
            errors="\n".join(errors_list) if errors_list else None
        )
        db.add(run_record)
        db.commit()
    except Exception as e:
        logger.error(f"Erro geral no job refresh_tokens_job: {e}")
        try:
            run_record = SchedulerRun(
                job="refresh_tokens",
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                items_processed=items_processed,
                errors=str(e)
            )
            db.add(run_record)
            db.commit()
        except Exception as db_err:
            logger.error(f"Erro ao persistir erro de scheduler no banco: {db_err}")
    finally:
        db.close()


def sync_orders_job():
    logger.info("Iniciando job sync_orders_job...")
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    items_processed = 0
    errors_list = []
    
    try:
        from backend.app.services.order_service import sync_all_orders
        tenants = db.query(Tenant).all()
        for tenant in tenants:
            res = sync_all_orders(db, tenant.id)
            items_processed += res.get("synced", 0)
        
        run_record = SchedulerRun(
            job="sync_orders",
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            items_processed=items_processed,
            errors=None
        )
        db.add(run_record)
        db.commit()
    except Exception as e:
        logger.error(f"Erro geral no job sync_orders_job: {e}")
        try:
            run_record = SchedulerRun(
                job="sync_orders",
                start_time=start_time,
                end_time=datetime.now(timezone.utc),
                items_processed=items_processed,
                errors=str(e)
            )
            db.add(run_record)
            db.commit()
        except Exception as db_err:
            logger.error(f"Erro ao persistir erro de scheduler no banco: {db_err}")
    finally:
        db.close()


def generate_alerts_job():
    logger.info("Iniciando job generate_alerts_job...")
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    items_processed = 0
    
    try:
        from backend.app.services.alert_service import check_and_generate_alerts
        tenants = db.query(Tenant).all()
        for tenant in tenants:
            created = check_and_generate_alerts(db, tenant.id)
            items_processed += created
        
        run_record = SchedulerRun(
            job="generate_alerts",
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            items_processed=items_processed,
            errors=None
        )
        db.add(run_record)
        db.commit()
    except Exception as e:
        logger.error(f"[Scheduler] Erro global na verificação de alertas: {e}")
    finally:
        db.close()

def check_stock_reconciliation_job():
    """Roda a reconciliação de estoque de forma assíncrona para todos os tenants."""
    logger.info("[Scheduler] Iniciando verificação de reconciliação de estoque (todos os tenants)...")
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).all()
        for t in tenants:
            try:
                reconcile_stock(t.id, db)
            except Exception as e:
                logger.error(f"[Scheduler] Erro ao reconciliar estoque do tenant {t.id}: {e}")
    except Exception as e:
        logger.error(f"[Scheduler] Erro global na reconciliação de estoque: {e}")
    finally:
        db.close()


def start_scheduler():
    global scheduler
    if os.getenv("VERCEL"):
        logger.info("Ambiente serverless (VERCEL) detectado. BackgroundScheduler NÃO inicializado.")
        return
        
    scheduler = BackgroundScheduler()
    
    scheduler.add_job(
        sync_bling_stock_job,
        "interval",
        minutes=SCHEDULER_SYNC_STOCK_MINUTES,
        id="sync_bling_stock",
        replace_existing=True
    )
    scheduler.add_job(
        sync_ml_questions_job,
        "interval",
        minutes=SCHEDULER_SYNC_QUESTIONS_MINUTES,
        id="sync_ml_questions",
        replace_existing=True
    )
    scheduler.add_job(
        refresh_tokens_job,
        "interval",
        minutes=SCHEDULER_REFRESH_TOKENS_MINUTES,
        id="refresh_tokens",
        replace_existing=True
    )
    scheduler.add_job(
        generate_alerts_job,
        "interval",
        minutes=SCHEDULER_CHECK_ALERTS_MINUTES,
        id="generate_alerts",
        replace_existing=True
    )
    scheduler.add_job(
        sync_orders_job,
        "interval",
        minutes=15,
        id="sync_orders",
        replace_existing=True
    )
    
    # 5. Job de reconciliação de estoque
    minutes_reconciliation = int(os.getenv("SCHEDULER_RECONCILIATION_MINUTES", "30"))
    scheduler.add_job(
        check_stock_reconciliation_job,
        "interval",
        minutes=minutes_reconciliation,
        id="stock_reconciliation",
        replace_existing=True
    )
    logger.info(f"[Scheduler] Job de reconciliação configurado (intervalo: {minutes_reconciliation} min).")
    
    scheduler.start()
    logger.info("BackgroundScheduler iniciado com sucesso.")


def shutdown_scheduler():
    global scheduler
    if scheduler:
        scheduler.shutdown()
        logger.info("BackgroundScheduler finalizado com sucesso.")
